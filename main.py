"""Event attendance and leadership-roll Discord bot.

Set DISCORD_TOKEN in .env, invite the bot with the Message Content intent,
and ensure the bot can read member roles, send messages, and manage messages.
"""

import io
import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import discord
from discord.ext import commands
from dotenv import load_dotenv


OFFICER_ROLE = "Officers"
NCO_ROLE = "Non-Commission Officer"
ENLISTED_ROLE = "Enlisted"
ATTENDANCE_ROLE = "Attendance"
DATABASE_PATH = Path(__file__).with_name("attendance.sqlite3")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rollit")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AttendanceDatabase:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS member_attendance (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                events_attended INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                started_by INTEGER NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS event_attendees (
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                rank TEXT NOT NULL,
                PRIMARY KEY (event_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS admin_panels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS leaderboard_panels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL PRIMARY KEY
            );
            """
        )
        self.connection.commit()

    def create_event(self, guild_id: int, channel_id: int, message_id: int, started_by: int, roster: Iterable["Attendee"]) -> int:
        cursor = self.connection.execute(
            "INSERT INTO events (guild_id, channel_id, message_id, started_by) VALUES (?, ?, ?, ?)",
            (guild_id, channel_id, message_id, started_by),
        )
        event_id = cursor.lastrowid
        self.connection.executemany(
            "INSERT INTO event_attendees (event_id, user_id, display_name, rank) VALUES (?, ?, ?, ?)",
            [(event_id, person.user_id, person.display_name, person.rank) for person in roster],
        )
        self.connection.commit()
        return int(event_id)

    def complete_event(self, event_id: int, guild_id: int, attendees: Iterable["Attendee"]) -> bool:
        cursor = self.connection.execute(
            "UPDATE events SET completed_at = ? WHERE id = ? AND completed_at IS NULL",
            (utc_now(), event_id),
        )
        if cursor.rowcount != 1:
            self.connection.rollback()
            return False
        self.connection.executemany(
            """
            INSERT INTO member_attendance (guild_id, user_id, events_attended)
            VALUES (?, ?, 1)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET events_attended = events_attended + 1
            """,
            [(guild_id, person.user_id) for person in attendees],
        )
        self.connection.commit()
        return True

    def attendance_count(self, guild_id: int, user_id: int) -> int:
        row = self.connection.execute(
            "SELECT events_attended FROM member_attendance WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return int(row[0]) if row else 0

    def add_event_attendee(self, event_id: int, attendee: "Attendee") -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO event_attendees (event_id, user_id, display_name, rank)
            VALUES (?, ?, ?, ?)
            """,
            (event_id, attendee.user_id, attendee.display_name, attendee.rank),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def add_admin_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO admin_panels (guild_id, channel_id, message_id) VALUES (?, ?, ?)",
            (guild_id, channel_id, message_id),
        )
        self.connection.commit()

    def admin_panels(self, guild_id: int) -> list[tuple[int, int]]:
        return [
            (int(row[0]), int(row[1]))
            for row in self.connection.execute(
                "SELECT channel_id, message_id FROM admin_panels WHERE guild_id = ?", (guild_id,)
            )
        ]

    def remove_admin_panel(self, message_id: int) -> None:
        self.connection.execute("DELETE FROM admin_panels WHERE message_id = ?", (message_id,))
        self.connection.commit()

    def add_leaderboard_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO leaderboard_panels (guild_id, channel_id, message_id) VALUES (?, ?, ?)",
            (guild_id, channel_id, message_id),
        )
        self.connection.commit()

    def leaderboard_panels(self, guild_id: int) -> list[tuple[int, int]]:
        return [
            (int(row[0]), int(row[1]))
            for row in self.connection.execute(
                "SELECT channel_id, message_id FROM leaderboard_panels WHERE guild_id = ?", (guild_id,)
            )
        ]

    def remove_leaderboard_panel(self, message_id: int) -> None:
        self.connection.execute("DELETE FROM leaderboard_panels WHERE message_id = ?", (message_id,))
        self.connection.commit()

    def attendance_rows(self, guild_id: int) -> list[tuple[int, int]]:
        return [
            (int(row[0]), int(row[1]))
            for row in self.connection.execute(
                """
                SELECT user_id, events_attended FROM member_attendance
                WHERE guild_id = ? AND events_attended > 0
                ORDER BY events_attended DESC, user_id
                """,
                (guild_id,),
            )
        ]

    def set_attendance_count(self, guild_id: int, user_id: int, count: int) -> None:
        if count < 0:
            raise ValueError("Attendance cannot be negative.")
        self.connection.execute(
            """
            INSERT INTO member_attendance (guild_id, user_id, events_attended)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET events_attended = excluded.events_attended
            """,
            (guild_id, user_id, count),
        )
        self.connection.commit()


@dataclass(frozen=True)
class Attendee:
    user_id: int
    display_name: str
    mention: str
    rank: str


def role_names(member: discord.Member) -> set[str]:
    return {role.name.casefold() for role in member.roles}


def rank_for(member: discord.Member) -> str | None:
    names = role_names(member)
    if OFFICER_ROLE.casefold() in names:
        return "Officer"
    if NCO_ROLE.casefold() in names:
        return "Non-Commission Officer"
    if ENLISTED_ROLE.casefold() in names:
        return "Enlisted"
    return None


def has_attendance(member: discord.Member) -> bool:
    return ATTENDANCE_ROLE.casefold() in role_names(member)


def roster_for(guild: discord.Guild) -> list[Attendee]:
    roster = []
    for member in guild.members:
        if member.bot or not has_attendance(member):
            continue
        rank = rank_for(member)
        if rank:
            roster.append(Attendee(member.id, member.display_name, member.mention, rank))
    return sorted(roster, key=lambda person: (person.rank, person.display_name.casefold()))


def roster_text(roster: list[Attendee]) -> str:
    if not roster:
        return "*No members currently have both Attendance and a recognized rank.*"
    lines = [f"{person.mention} — {person.rank}" for person in roster]
    return "\n".join(lines)


def roster_embed(roster: list[Attendee], state: str = "Waiting to roll") -> discord.Embed:
    embed = discord.Embed(title="Event Attendance", colour=discord.Colour.blurple())
    text = roster_text(roster)
    # Discord limits an embed description to 4,096 characters. A full roster is
    # still supplied as an attachment when it does not fit in the post.
    if len(text) <= 3_900:
        embed.description = f"**{len(roster)} attendee(s) checked in**\n{text}"
    else:
        embed.description = f"**{len(roster)} attendee(s) checked in**\nFull roster is attached as `attendance-roster.txt`."
    embed.set_footer(text=state)
    return embed


def roster_file(roster: list[Attendee]) -> discord.File | None:
    text = roster_text(roster)
    if len(text) <= 3_900:
        return None
    return discord.File(fp=io.BytesIO(text.encode("utf-8")), filename="attendance-roster.txt")


def administration_embed(guild: discord.Guild) -> discord.Embed:
    """Create the public attendance summary, within Discord embed limits."""
    rows = db.attendance_rows(guild.id)
    entries = []
    for user_id, total in rows:
        member = guild.get_member(user_id)
        name = member.display_name if member else f"Former member ({user_id})"
        entries.append(f"{name} — **{total}**")
    text = "\n".join(entries) or "*No completed event attendance has been recorded yet.*"
    if len(text) > 3_900:
        text = text[:3_850].rsplit("\n", 1)[0] + "\n*…additional members not shown*"
    embed = discord.Embed(title="BakeBot Attendance Administration", colour=discord.Colour.gold())
    embed.description = text
    embed.set_footer(text="Choose a member below to add, subtract, or set their attendance total.")
    return embed


def leaderboard_embed(guild: discord.Guild) -> discord.Embed:
    """Create a public standings board from completed event attendance."""
    lines = []
    for position, (user_id, total) in enumerate(db.attendance_rows(guild.id), start=1):
        member = guild.get_member(user_id)
        name = member.display_name if member else f"Former member ({user_id})"
        lines.append(f"**{position}.** {name} — **{total}** event(s)")
    text = "\n".join(lines) or "*No completed event attendance has been recorded yet.*"
    if len(text) > 3_900:
        text = text[:3_850].rsplit("\n", 1)[0] + "\n*…additional members not shown*"
    embed = discord.Embed(title="Event Attendance Leaderboard", description=text, colour=discord.Colour.gold())
    embed.set_footer(text="Updates automatically when event attendance changes.")
    return embed


async def refresh_administration_panel(message: discord.Message, guild: discord.Guild) -> None:
    await message.edit(embed=administration_embed(guild), view=AdminPanelView())


async def refresh_administration_panels(guild: discord.Guild) -> None:
    """Refresh every panel previously posted by !adminpanel in this server."""
    for channel_id, message_id in db.admin_panels(guild.id):
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            db.remove_admin_panel(message_id)
            continue
        try:
            message = await channel.fetch_message(message_id)
            await refresh_administration_panel(message, guild)
        except (discord.NotFound, discord.Forbidden):
            db.remove_admin_panel(message_id)
        except discord.HTTPException:
            log.exception("Could not refresh administration panel %s", message_id)


async def refresh_leaderboards(guild: discord.Guild) -> None:
    for channel_id, message_id in db.leaderboard_panels(guild.id):
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            db.remove_leaderboard_panel(message_id)
            continue
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=leaderboard_embed(guild))
        except (discord.NotFound, discord.Forbidden):
            db.remove_leaderboard_panel(message_id)
        except discord.HTTPException:
            log.exception("Could not refresh leaderboard %s", message_id)


async def refresh_attendance_displays(guild: discord.Guild) -> None:
    await refresh_administration_panels(guild)
    await refresh_leaderboards(guild)


async def remove_command_message(ctx: commands.Context) -> None:
    """Silently remove a recognised prefix command when permissions allow it."""
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        log.warning("Could not delete command message %s", ctx.message.id)


class SetAttendanceModal(discord.ui.Modal, title="Set events attended"):
    total = discord.ui.TextInput(label="Events attended", placeholder="0", max_length=8)

    def __init__(self, target: discord.Member, panel_message: discord.Message) -> None:
        super().__init__()
        self.target = target
        self.panel_message = panel_message
        self.total.default = str(db.attendance_count(target.guild.id, target.id))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            total = int(str(self.total.value).strip())
            if total < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Enter a whole number of zero or greater.", ephemeral=True)
            return
        db.set_attendance_count(self.target.guild.id, self.target.id, total)
        await refresh_attendance_displays(self.target.guild)
        await interaction.response.send_message(f"Set {self.target.mention}'s total to **{total}**.", ephemeral=True)


class MemberAttendanceView(discord.ui.View):
    def __init__(self, target: discord.Member, panel_message: discord.Message) -> None:
        super().__init__(timeout=300)
        self.target = target
        self.panel_message = panel_message

    async def change_by(self, interaction: discord.Interaction, amount: int) -> None:
        current = db.attendance_count(self.target.guild.id, self.target.id)
        total = max(0, current + amount)
        db.set_attendance_count(self.target.guild.id, self.target.id, total)
        await refresh_attendance_displays(self.target.guild)
        await interaction.response.edit_message(
            content=f"Editing {self.target.mention}: **{total}** event(s) attended.", view=self
        )

    @discord.ui.button(label="−1", style=discord.ButtonStyle.danger)
    async def subtract(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.change_by(interaction, -1)

    @discord.ui.button(label="+1", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.change_by(interaction, 1)

    @discord.ui.button(label="Set total", style=discord.ButtonStyle.primary)
    async def set_total(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(SetAttendanceModal(self.target, self.panel_message))


class AdminMemberSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Select a member to edit attendance…", min_values=1, max_values=1, custom_id="bakebot:admin-member")

    async def callback(self, interaction: discord.Interaction) -> None:
        target = self.values[0]
        if not isinstance(target, discord.Member) or interaction.message is None:
            await interaction.response.send_message("I could not resolve that server member.", ephemeral=True)
            return
        total = db.attendance_count(target.guild.id, target.id)
        await interaction.response.send_message(
            f"Editing {target.mention}: **{total}** event(s) attended.",
            view=MemberAttendanceView(target, interaction.message),
            ephemeral=True,
        )


class AdminPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(AdminMemberSelect())


class LeaderboardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, owner: "LeaderboardChannelView") -> None:
        super().__init__(
            placeholder="Choose a channel for the leaderboard…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner.invoker_id:
            await interaction.response.send_message("Only the person who used !leaderboard can choose its channel.", ephemeral=True)
            return
        selected_channel = self.values[0]
        if interaction.guild is None:
            await interaction.response.send_message("Choose a server text channel.", ephemeral=True)
            return
        # ChannelSelect supplies an AppCommandChannel in many discord.py
        # versions, so resolve it from the guild cache before type checking.
        channel = interaction.guild.get_channel(selected_channel.id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Choose a server text channel.", ephemeral=True)
            return
        self.owner.channel = channel
        self.owner.submit.disabled = False
        await interaction.response.edit_message(
            content=f"Leaderboard channel selected: {channel.mention}. Click **Post leaderboard** to confirm.",
            view=self.owner,
        )


class LeaderboardChannelView(discord.ui.View):
    def __init__(self, invoker_id: int) -> None:
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.channel: discord.TextChannel | None = None
        self.add_item(LeaderboardChannelSelect(self))

    @discord.ui.button(label="Post leaderboard", style=discord.ButtonStyle.success, disabled=True)
    async def submit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the person who used !leaderboard can post it.", ephemeral=True)
            return
        if self.channel is None or interaction.guild is None:
            await interaction.response.send_message("Choose a channel first.", ephemeral=True)
            return
        me = interaction.guild.me
        if me is None or not self.channel.permissions_for(me).send_messages:
            await interaction.response.send_message("I cannot post in that channel. Check my channel permissions.", ephemeral=True)
            return
        post = await self.channel.send(embed=leaderboard_embed(interaction.guild))
        db.add_leaderboard_panel(interaction.guild.id, self.channel.id, post.id)
        await interaction.response.edit_message(content=f"Leaderboard posted in {self.channel.mention}.", view=None)


class RollItView(discord.ui.View):
    def __init__(self, db: AttendanceDatabase, event_id: int, guild_id: int, invoker_id: int, roster: list[Attendee]) -> None:
        super().__init__(timeout=None)
        self.db = db
        self.event_id = event_id
        self.guild_id = guild_id
        self.invoker_id = invoker_id
        self.roster = roster
        self.rolled = False
        self.completed = False
        self.selection: tuple[Attendee, list[Attendee]] | None = None
        self.last_roller: str | None = None
        self.next_roll_at = 0.0

    def event_embed(self) -> discord.Embed:
        embed = roster_embed(self.roster, "Leadership selection complete" if self.selection else "Waiting to roll")
        if self.selection:
            officer, sgts = self.selection
            embed.add_field(name="Officer", value=officer.mention, inline=False)
            embed.add_field(name="Sergeants", value="\n".join(person.mention for person in sgts), inline=False)
        if self.last_roller:
            embed.add_field(name="Last rolled by", value=self.last_roller, inline=False)
        return embed

    def add_late_member(self, member: discord.Member) -> Attendee | None:
        rank = rank_for(member)
        if member.bot or rank is None:
            return None
        attendee = Attendee(member.id, member.display_name, member.mention, rank)
        if attendee.user_id not in {person.user_id for person in self.roster}:
            self.roster.append(attendee)
            self.roster.sort(key=lambda person: (person.rank, person.display_name.casefold()))
            self.db.add_event_attendee(self.event_id, attendee)
        return attendee

    async def _may_manage(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in {self.invoker_id, interaction.guild.owner_id if interaction.guild else 0}:
            return True
        await interaction.response.send_message("Only the event starter or server owner can use this.", ephemeral=True)
        return False

    @discord.ui.button(label="Roll the Dice", style=discord.ButtonStyle.primary, emoji="🎲")
    async def roll_dice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not has_attendance(interaction.user):
            await interaction.response.send_message("Only members with @Attendance can roll the dice.", ephemeral=True)
            return
        seconds_remaining = self.next_roll_at - time.monotonic()
        if seconds_remaining > 0:
            await interaction.response.send_message(
                f"The dice can be rolled again in **{int(seconds_remaining) + 1} seconds**.", ephemeral=True
            )
            return

        officers = [person for person in self.roster if person.rank == "Officer"]
        sgt_pool = [person for person in self.roster if person.rank in {"Officer", "Non-Commission Officer"}]
        if not officers or len(sgt_pool) < 3:
            await interaction.response.send_message(
                "Cannot roll: need at least 1 Officer and 3 total Officer/NCO attendees so the selections are unique.",
                ephemeral=True,
            )
            return

        officer = random.choice(officers)
        sgts = random.sample([person for person in sgt_pool if person.user_id != officer.user_id], 2)
        self.rolled = True
        self.selection = (officer, sgts)
        self.last_roller = interaction.user.mention
        self.next_roll_at = time.monotonic() + 60
        await interaction.response.edit_message(embed=self.event_embed(), view=self)

    @discord.ui.button(label="END EVENT", style=discord.ButtonStyle.danger, emoji="🛑")
    async def finish_attendance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or OFFICER_ROLE.casefold() not in role_names(interaction.user):
            await interaction.response.send_message("Only members with @Officers can end this event.", ephemeral=True)
            return
        if self.completed:
            await interaction.response.send_message("Attendance has already been recorded for this event.", ephemeral=True)
            return
        if not self.db.complete_event(self.event_id, self.guild_id, self.roster):
            self.completed = True
            await interaction.response.send_message("Attendance was already recorded for this event.", ephemeral=True)
            return
        self.completed = True
        button.disabled = True
        removed_roles = 0
        if interaction.guild:
            attendance_role = discord.utils.get(interaction.guild.roles, name=ATTENDANCE_ROLE)
            if attendance_role is None:
                log.warning("Event %s completed but the Attendance role was not found", self.event_id)
            else:
                for attendee in self.roster:
                    member = interaction.guild.get_member(attendee.user_id)
                    if member is None or attendance_role not in member.roles:
                        continue
                    try:
                        await member.remove_roles(
                            attendance_role,
                            reason=f"Attendance finalized for event {self.event_id}",
                        )
                        removed_roles += 1
                    except (discord.Forbidden, discord.HTTPException):
                        log.exception("Could not remove Attendance from %s after event %s", attendee.user_id, self.event_id)
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else roster_embed(self.roster)
        embed = embed.copy()
        embed.set_footer(text=f"Attendance recorded for {len(self.roster)} member(s); @Attendance removed from {removed_roles}")
        await interaction.response.edit_message(embed=embed, view=self)
        if interaction.message:
            try:
                await interaction.message.delete()
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not remove completed event post %s", interaction.message.id)
        if interaction.guild:
            await refresh_attendance_displays(interaction.guild)

    @discord.ui.button(label="Late attendance", style=discord.ButtonStyle.secondary, emoji="⏱️", custom_id="bakebot:late-attendance")
    async def late_attendance(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.completed or interaction.message is None:
            await interaction.response.send_message("This event's attendance has already been recorded.", ephemeral=True)
            return
        target = interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("This button can only be used by a server member.", ephemeral=True)
            return
        attendance_role = discord.utils.get(target.guild.roles, name=ATTENDANCE_ROLE)
        if attendance_role is None:
            await interaction.response.send_message("I cannot find the @Attendance role.", ephemeral=True)
            return
        if attendance_role not in target.roles:
            try:
                await target.add_roles(attendance_role, reason=f"Late attendance for event {self.event_id}")
            except discord.Forbidden:
                await interaction.response.send_message("I need Manage Roles permission and a role position above @Attendance.", ephemeral=True)
                return
        was_on_roster = target.id in {person.user_id for person in self.roster}
        attendee = self.add_late_member(target)
        if attendee is None:
            await interaction.response.send_message(
                "You received @Attendance, but have no recognized event rank and cannot be added to this roster.", ephemeral=True
            )
            return
        reroll_note = ""
        if not was_on_roster and self.rolled:
            self.rolled = False
            self.selection = None
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.label == "Roll the Dice":
                    child.disabled = False
            reroll_note = " The previous roll was reset; roll again to include them."
        await interaction.message.edit(embed=self.event_embed(), view=self)
        await interaction.response.send_message(
            f"You were added as a late attendee and are now included in this event's roster.{reroll_note}", ephemeral=True
        )


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
db = AttendanceDatabase(DATABASE_PATH)


@bot.event
async def setup_hook() -> None:
    # Register the no-timeout public view so an existing admin panel still
    # accepts selections after the bot restarts.
    bot.add_view(AdminPanelView())


@bot.event
async def on_ready() -> None:
    log.info("Connected as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.command(name="ROLLIT")
@commands.guild_only()
async def rollit(ctx: commands.Context) -> None:
    assert ctx.guild is not None
    await remove_command_message(ctx)
    invoker_rank = rank_for(ctx.author)
    if invoker_rank == "Enlisted":
        return  # Enlisted members are deliberately ignored.
    if invoker_rank is None:
        return
    roster = roster_for(ctx.guild)
    embed = roster_embed(roster)
    post = await ctx.send(embed=embed, file=roster_file(roster))
    event_id = db.create_event(ctx.guild.id, ctx.channel.id, post.id, ctx.author.id, roster)
    await post.edit(view=RollItView(db, event_id, ctx.guild.id, ctx.author.id, roster))


@bot.command(name="status")
@commands.guild_only()
async def status(ctx: commands.Context) -> None:
    """DM the caller's attendance total and current role eligibility."""
    assert ctx.guild is not None
    await remove_command_message(ctx)
    rank = rank_for(ctx.author)
    if rank == "Officer":
        eligible = "Officer or Sergeant"
    elif rank == "Non-Commission Officer":
        eligible = "Sergeant"
    elif rank == "Enlisted":
        eligible = "Attendance only (not eligible for Officer or Sergeant)"
    else:
        eligible = "No recognized event rank"
    count = db.attendance_count(ctx.guild.id, ctx.author.id)
    try:
        await ctx.author.send(f"**Event status for {ctx.guild.name}**\nEvents attended: **{count}**\nEligible role: **{eligible}**")
    except discord.Forbidden:
        await ctx.send("I could not DM your status. Enable DMs from server members and try again.", delete_after=15)


@bot.command(name="adminpanel")
@commands.guild_only()
async def adminpanel(ctx: commands.Context) -> None:
    """Post the public attendance editor in #bakebot-center."""
    assert ctx.guild is not None
    await remove_command_message(ctx)
    channel = discord.utils.get(ctx.guild.text_channels, name="bakebot-center")
    if channel is None:
        await ctx.send("I cannot find a text channel named `#bakebot-center`.", delete_after=15)
        return
    if not channel.permissions_for(ctx.guild.me).send_messages:
        await ctx.send("I cannot post in `#bakebot-center`. Check my channel permissions.", delete_after=15)
        return
    post = await channel.send(embed=administration_embed(ctx.guild), view=AdminPanelView())
    db.add_admin_panel(ctx.guild.id, channel.id, post.id)
    await ctx.send(f"Administration panel posted in {channel.mention}.", delete_after=15)


@bot.command(name="leaderboard")
@commands.guild_only()
async def leaderboard(ctx: commands.Context) -> None:
    """Ask the command caller where to post an auto-updating standings board."""
    await remove_command_message(ctx)
    await ctx.send(
        "Choose the channel where the event attendance leaderboard should be posted.",
        view=LeaderboardChannelView(ctx.author.id),
        ephemeral=True,
    )


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to the .env file.")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
