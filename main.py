
"""Event attendance and leadership-roll Discord bot.

Set DISCORD_TOKEN in .env, invite the bot with the Message Content intent,
and ensure the bot can read member roles, send messages, and manage messages.
"""

import asyncio
import io
import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

"""BakeBot: randomly assign an Officer and Sergeant for a Discord event."""

import logging
import os
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv



OFFICER_ROLE = "Officers"
NCO_ROLE = "Non-Commission Officer"
NCO_ROLE_ALIASES = (NCO_ROLE, "Non-Commissioned Officers")
ENLISTED_ROLE = "Enlisted"
ATTENDANCE_ROLE = "Attendance"
ACTIVE_DUTY_ROLE = "Active Duty"
DATABASE_PATH = Path(__file__).with_name("attendance.sqlite3")
active_event_wizards: set[int] = set()

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


def leaderboard_content(guild: discord.Guild) -> str:
    """Create a public standings board using Discord's native Markdown sizes."""
    lines = ["# Event Attendance Leaderboard", ""]
    medals = ("🥇", "🥈", "🥉")
    for position, (user_id, total) in enumerate(db.attendance_rows(guild.id), start=1):
        member = guild.get_member(user_id)
        name = member.display_name if member else f"Former member ({user_id})"
        if position <= 3:
            lines.append(f"## {medals[position - 1]} {name} — **{total}**")
        else:
            lines.append(f"**{position}.** {name} — **{total}**")
    if len(lines) == 2:
        lines.append("*No completed event attendance has been recorded yet.*")
    lines.extend(("", "*Updates automatically when event attendance changes.*"))
    content = "\n".join(lines)
    if len(content) > 2_000:
        content = content[:1_950].rsplit("\n", 1)[0] + "\n*…additional members not shown*"
    return content


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
            await message.edit(content=leaderboard_content(guild), embed=None)
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
        post = await self.channel.send(content=leaderboard_content(interaction.guild))
        db.add_leaderboard_panel(interaction.guild.id, self.channel.id, post.id)
        await interaction.response.edit_message(content=f"Leaderboard posted in {self.channel.mention}.", view=None)


class AdminSayChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, owner: "AdminSayChannelView") -> None:
        super().__init__(
            placeholder="Choose a channel to post the message…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner.author_id:
            await interaction.response.send_message("Only the person who used !adminsay can choose the channel.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Choose a server text channel.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(self.values[0].id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Choose a server text channel.", ephemeral=True)
            return
        self.owner.channel = channel
        self.owner.submit.disabled = False
        await interaction.response.edit_message(
            content=f"Post the prepared message in {channel.mention}? Click **Post message** to confirm.",
            view=self.owner,
        )


class AdminSayChannelView(discord.ui.View):
    def __init__(self, author_id: int, message_text: str, dm_prompt: discord.Message) -> None:
        super().__init__(timeout=120)
        self.author_id = author_id
        self.message_text = message_text
        self.dm_prompt = dm_prompt
        self.channel: discord.TextChannel | None = None
        self.add_item(AdminSayChannelSelect(self))

    @discord.ui.button(label="Post message", style=discord.ButtonStyle.success, disabled=True)
    async def submit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who used !adminsay can post the message.", ephemeral=True)
            return
        if self.channel is None or interaction.guild is None:
            await interaction.response.send_message("Choose a channel first.", ephemeral=True)
            return
        me = interaction.guild.me
        if me is None or not self.channel.permissions_for(me).send_messages:
            await interaction.response.send_message("I cannot post in that channel. Check my channel permissions.", ephemeral=True)
            return
        await self.channel.send(self.message_text, allowed_mentions=discord.AllowedMentions.none())
        await self.dm_prompt.edit(content=f"Posted your message in **#{self.channel.name}**.")
        await interaction.response.edit_message(content=f"Message posted in {self.channel.mention}.", view=None)


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


def event_embed(event: "ScheduledEventView") -> discord.Embed:
    accepted = "\n".join(member.mention for member in event.accepted.values()) or "No accepted attendees yet"
    tentative = "\n".join(member.mention for member in event.tentative.values()) or "No tentative attendees yet"
    embed = discord.Embed(title=event.title, description=event.description, colour=discord.Colour.gold())
    time_label = event.starts_at.strftime("%A, %B %d, %Y at %I:%M %p").replace(" 0", " ")
    embed.add_field(name="Time", value=time_label, inline=False)
    embed.add_field(name=f"✅ Accepted ({len(event.accepted)})", value=accepted, inline=True)
    embed.add_field(name=f"❓ Tentative ({len(event.tentative)})", value=tentative, inline=True)
    if event.last_sergeants:
        embed.add_field(
            name="Next-round Sergeants",
            value="\n".join(member.mention for member in event.last_sergeants),
            inline=False,
        )
    embed.set_footer(text=f"Created by {event.organizer.display_name}")
    return embed


class ManualEventMemberSelect(discord.ui.UserSelect):
    def __init__(self, event: "ScheduledEventView") -> None:
        super().__init__(placeholder="Select a member to add…", min_values=1, max_values=1)
        self.event = event

    async def callback(self, interaction: discord.Interaction) -> None:
        member = self.values[0]
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("I could not resolve that server member.", ephemeral=True)
            return
        if await self.event.add_accepted(member, interaction):
            await interaction.response.edit_message(
                content=f"Added {member.mention} as accepted and assigned @Attendance.", view=None
            )


class ManualEventMemberView(discord.ui.View):
    def __init__(self, event: "ScheduledEventView") -> None:
        super().__init__(timeout=120)
        self.add_item(ManualEventMemberSelect(event))


class ScheduledEventView(discord.ui.View):
    def __init__(
        self,
        organizer: discord.Member,
        title: str,
        description: str,
        starts_at: datetime,
    ) -> None:
        super().__init__(timeout=None)
        self.organizer = organizer
        self.title = title
        self.description = description
        self.starts_at = starts_at
        self.accepted: dict[int, discord.Member] = {}
        self.tentative: dict[int, discord.Member] = {}
        self.message: discord.Message | None = None
        self.event_id: int | None = None
        self.finished = False
        self.last_sergeants: list[discord.Member] = []

    async def refresh_post(self) -> None:
        if self.message:
            await self.message.edit(embed=event_embed(self), view=self)

    async def add_accepted(self, member: discord.Member, interaction: discord.Interaction) -> bool:
        role = discord.utils.get(member.guild.roles, name=ATTENDANCE_ROLE)
        if role is None:
            await interaction.response.send_message("I cannot find the @Attendance role.", ephemeral=True)
            return False
        if role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Accepted RSVP: {self.title}")
            except discord.Forbidden:
                await interaction.response.send_message("I need Manage Roles permission and a role position above @Attendance.", ephemeral=True)
                return False
        self.accepted[member.id] = member
        self.tentative.pop(member.id, None)
        if self.event_id is not None:
            db.add_event_attendee(
                self.event_id, Attendee(member.id, member.display_name, member.mention, rank_for(member) or "Unranked")
            )
        await self.refresh_post()
        return True

    @discord.ui.button(label="Accepted", style=discord.ButtonStyle.success, emoji="✅")
    async def accepted_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("You must be a server member to sign up.", ephemeral=True)
            return
        if await self.add_accepted(interaction.user, interaction):
            await interaction.response.send_message("You are marked as accepted and have @Attendance.", ephemeral=True)

    @discord.ui.button(label="Tentative", style=discord.ButtonStyle.secondary, emoji="❓")
    async def tentative_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("You must be a server member to sign up.", ephemeral=True)
            return
        self.tentative[interaction.user.id] = interaction.user
        self.accepted.pop(interaction.user.id, None)
        await self.refresh_post()
        await interaction.response.send_message("You are marked as tentative.", ephemeral=True)

    @discord.ui.button(label="Add member", style=discord.ButtonStyle.primary, emoji="➕")
    async def add_member_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        allowed = isinstance(interaction.user, discord.Member) and OFFICER_ROLE.casefold() in role_names(interaction.user)
        if not allowed:
            await interaction.response.send_message("Only members with @Officers can add members.", ephemeral=True)
            return
        await interaction.response.send_message("Select a member to add.", view=ManualEventMemberView(self), ephemeral=True)

    @discord.ui.button(label="Roll Sgt", style=discord.ButtonStyle.secondary, emoji="🎲")
    async def roll_sergeants(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or OFFICER_ROLE.casefold() not in role_names(interaction.user):
            await interaction.response.send_message("Only members with @Officers can roll Sergeants.", ephemeral=True)
            return
        eligible = [
            member
            for member in self.accepted.values()
            if not member.bot and (
                OFFICER_ROLE.casefold() in role_names(member)
                or any(role.casefold() in role_names(member) for role in NCO_ROLE_ALIASES)
            )
        ]
        if len(eligible) < 2:
            await interaction.response.send_message(
                "At least two Accepted members with @Officers or @Non-Commissioned Officers are needed to roll Sergeants.",
                ephemeral=True,
            )
            return
        self.last_sergeants = random.sample(eligible, 2)
        await interaction.response.edit_message(embed=event_embed(self), view=self)

    @discord.ui.button(label="END EVENT", style=discord.ButtonStyle.danger, emoji="🛑")
    async def end_event_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or OFFICER_ROLE.casefold() not in role_names(interaction.user):
            await interaction.response.send_message("Only members with @Officers can end this event.", ephemeral=True)
            return
        if self.finished or self.event_id is None:
            await interaction.response.send_message("This event has already been ended.", ephemeral=True)
            return
        attendees = [
            Attendee(member.id, member.display_name, member.mention, rank_for(member) or "Unranked")
            for member in self.accepted.values()
        ]
        if not db.complete_event(self.event_id, interaction.guild.id, attendees):
            await interaction.response.send_message("This event has already been submitted.", ephemeral=True)
            return
        self.finished = True
        role = discord.utils.get(interaction.guild.roles, name=ATTENDANCE_ROLE)
        if role:
            for member in interaction.guild.members:
                if role in member.roles:
                    try:
                        await member.remove_roles(role, reason=f"Event ended: {self.title}")
                    except (discord.Forbidden, discord.HTTPException):
                        log.exception("Could not remove Attendance from %s", member.id)
        await refresh_attendance_displays(interaction.guild)
        await interaction.response.edit_message(content="Event ended and attendance submitted.", embed=None, view=None)


def parse_event_time(day: str, text: str) -> datetime | None:
    formats = ("%I:%M %p", "%I %p", "%H:%M", "%H")
    parsed = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(text.strip().upper(), fmt).time()
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    target_date = datetime.now().date() + timedelta(days=1 if day == "tomorrow" else 0)
    return datetime.combine(target_date, parsed)


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


async def ask_event_question(user: discord.Member, question: str) -> str | None:
    await user.send(question)

    def is_reply(message: discord.Message) -> bool:
        return message.author.id == user.id and isinstance(message.channel, discord.DMChannel)

    try:
        reply = await bot.wait_for("message", check=is_reply, timeout=300)
    except asyncio.TimeoutError:
        await user.send("Event setup timed out. Run `!event` again when ready.")
        return None
    return reply.content.strip()


@bot.command(name="event")
@commands.guild_only()
async def create_event(ctx: commands.Context) -> None:
    """Silently collect event details by DM and publish an RSVP event post."""
    assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
    await remove_command_message(ctx)
    if ctx.author.id in active_event_wizards:
        return
    active_event_wizards.add(ctx.author.id)
    try:
        try:
            await ctx.author.send("Let's create an event. Reply to each question in this DM.")
        except discord.Forbidden:
            return
        day = (await ask_event_question(ctx.author, "1/5: Is the event for **today** or **tomorrow**?"))
        if day is None:
            return
        day = day.casefold()
        if day not in {"today", "tomorrow"}:
            await ctx.author.send("Please run `!event` again and answer `today` or `tomorrow`.")
            return
        time_text = await ask_event_question(ctx.author, "2/5: What time is the event? (Example: `8:00 PM`)")
        if time_text is None:
            return
        starts_at = parse_event_time(day, time_text)
        if starts_at is None:
            await ctx.author.send("I could not read that time. Run `!event` again using a time like `8:00 PM`.")
            return
        title = await ask_event_question(ctx.author, "3/5: What is the title of the event?")
        if not title:
            return
        description = await ask_event_question(ctx.author, "4/5: What description should appear under the title?")
        if description is None:
            return
        ping_answer = await ask_event_question(ctx.author, "5/5: Ping @Active Duty? Reply **yes** or **no**.")
        if ping_answer is None:
            return
        should_ping = ping_answer.casefold() in {"yes", "y"}

        view = ScheduledEventView(ctx.author, title, description, starts_at)
        content = None
        if should_ping:
            active_duty = discord.utils.get(ctx.guild.roles, name=ACTIVE_DUTY_ROLE)
            if active_duty:
                content = active_duty.mention
        post = await ctx.channel.send(
            content=content,
            embed=event_embed(view),
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=should_ping),
        )
        view.message = post
        view.event_id = db.create_event(ctx.guild.id, ctx.channel.id, post.id, ctx.author.id, [])
        await ctx.author.send(f"Your event, **{title}**, was posted in #{ctx.channel.name}.")
    finally:
        active_event_wizards.discard(ctx.author.id)


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


@bot.command(name="adminsay", help="Privately collect a message, then post it as the bot.")
@commands.guild_only()
async def adminsay_primary(ctx: commands.Context) -> None:
    """Delete the command, collect text/channel in DM, then post it as the bot."""
    await remove_command_message(ctx)
    try:
        prompt = await ctx.author.send("What should I say? Reply here within 2 minutes.")
    except discord.Forbidden:
        # A public prompt would defeat the command's silent behavior.
        return

    def is_author_dm(message: discord.Message) -> bool:
        return message.author.id == ctx.author.id and isinstance(message.channel, discord.DMChannel)

    try:
        response = await bot.wait_for("message", check=is_author_dm, timeout=120)
    except asyncio.TimeoutError:
        await prompt.edit(content="Timed out — no message was posted.")
        return

    await ctx.channel.send(
        "Choose the channel where the prepared admin message should be posted.",
        view=AdminSayChannelView(ctx.author.id, response.content, prompt),
        delete_after=120,
    )


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to the .env file.")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
OFFICER_ROLE = "Officers"
NON_COMMISSION_OFFICER_ROLE = "Non-Commission Officer"

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, case_insensitive=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}.")


class EventSignupView(discord.ui.View):
    """Keeps the two volunteer pools for one event message."""

    def __init__(self, event: str, organizer_id: int):
        super().__init__(timeout=None)
        self.event = event
        self.organizer_id = organizer_id
        self.officer_signups: dict[int, discord.Member] = {}
        self.sergeant_signups: dict[int, discord.Member] = {}
        self.enlisted_signups: dict[int, discord.Member] = {}
        self.last_officer: discord.Member | None = None
        self.last_sergeant: discord.Member | None = None
        self.message: discord.Message | None = None

    def signup_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"Signups: {self.event}",
            description="Select the position you are signing up for. Leadership roles require the listed Discord roles.",
            colour=discord.Colour.blurple(),
        )
        officers = ", ".join(member.mention for member in self.officer_signups.values()) or "No signups yet"
        sergeants = ", ".join(member.mention for member in self.sergeant_signups.values()) or "No signups yet"
        enlisted = ", ".join(member.mention for member in self.enlisted_signups.values()) or "No signups yet"
        embed.add_field(name=f"Officer candidates ({len(self.officer_signups)})", value=officers, inline=False)
        embed.add_field(name=f"Sergeant candidates ({len(self.sergeant_signups)})", value=sergeants, inline=False)
        embed.add_field(name=f"Enlisted attendees ({len(self.enlisted_signups)})", value=enlisted, inline=False)
        if self.last_officer and self.last_sergeant:
            embed.add_field(
                name="Current roll",
                value=f"Officer: {self.last_officer.mention}\nSergeant: {self.last_sergeant.mention}",
                inline=False,
            )
        embed.set_footer(text="The organizer or a server manager can randomize, re-roll, or delete this event.")
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.signup_embed(), view=self)

    @staticmethod
    def has_role(member: discord.Member, role_name: str) -> bool:
        return any(role.name.casefold() == role_name.casefold() for role in member.roles)

    @discord.ui.button(label="Officer Signup", style=discord.ButtonStyle.primary, row=0)
    async def officer_signup(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not self.has_role(interaction.user, OFFICER_ROLE):
            await interaction.response.send_message(
                "You need the @Officers role to sign up as Officer.", ephemeral=True
            )
            return
        self.officer_signups[interaction.user.id] = interaction.user
        await self.refresh(interaction)

    @discord.ui.button(label="Sergeant Signup", style=discord.ButtonStyle.secondary, row=0)
    async def sergeant_signup(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not (
            self.has_role(interaction.user, OFFICER_ROLE)
            or self.has_role(interaction.user, NON_COMMISSION_OFFICER_ROLE)
        ):
            await interaction.response.send_message(
                "You need the @Officers or @Non-Commission Officer role to sign up as Sergeant.",
                ephemeral=True,
            )
            return
        self.sergeant_signups[interaction.user.id] = interaction.user
        await self.refresh(interaction)

    @discord.ui.button(label="Enlisted Signup", style=discord.ButtonStyle.secondary, row=0)
    async def enlisted_signup(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("You must be a server member to sign up.", ephemeral=True)
            return
        self.enlisted_signups[interaction.user.id] = interaction.user
        await self.refresh(interaction)

    @discord.ui.button(label="Randomize Assignments", style=discord.ButtonStyle.success, row=1)
    async def randomize(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.roll(interaction)

    @discord.ui.button(label="Re-Roll", style=discord.ButtonStyle.success, row=1)
    async def reroll(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.roll(interaction, reroll=True)

    async def roll(self, interaction: discord.Interaction, reroll: bool = False) -> None:
        is_manager = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild
        if interaction.user.id != self.organizer_id and not is_manager:
            await interaction.response.send_message("Only the event organizer or a server manager can roll assignments.", ephemeral=True)
            return
        if reroll and not self.last_officer:
            await interaction.response.send_message("Make the first randomization before using Re-Roll.", ephemeral=True)
            return
        result = self.draw_assignments()
        if isinstance(result, str):
            await interaction.response.send_message(result, ephemeral=True)
            return
        officer, sergeant = result
        self.last_officer = officer
        self.last_sergeant = sergeant
        await interaction.response.edit_message(embed=self.signup_embed(), view=self)

    def draw_assignments(self) -> tuple[discord.Member, discord.Member] | str:
        """Draw distinct people from the current Officer and Sergeant pools."""
        if not self.officer_signups or not self.sergeant_signups:
            return "At least one signup is needed for each role before randomizing."
        officer = random.choice(list(self.officer_signups.values()))
        available_sergeants = [member for member in self.sergeant_signups.values() if member.id != officer.id]
        if not available_sergeants:
            return "There must be two different eligible people across the role signups."
        return officer, random.choice(available_sergeants)

    @discord.ui.button(label="Delete Event", style=discord.ButtonStyle.danger, row=1)
    async def delete_event(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        is_manager = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild
        if interaction.user.id != self.organizer_id and not is_manager:
            await interaction.response.send_message("Only the event organizer or a server manager can delete this event.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.message.delete()


@bot.command(name="ROLLIT", help="Create an Officer, Sergeant, and Enlisted sign-up panel.")
@commands.guild_only()
async def rollit(ctx: commands.Context, *, event: str):
    """Usage: !ROLLIT <event name>."""
    event = event.strip()
    view = EventSignupView(event, ctx.author.id)
    view.message = await ctx.send(embed=view.signup_embed(), view=view)


@rollit.error
async def rollit_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Usage: `{COMMAND_PREFIX}ROLLIT <event name>`")
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("This command can only be used in a server.")
        return
    raise error


@bot.command(help="Show how to create an event sign-up panel.")
async def assignhelp(ctx: commands.Context):
    await ctx.send(
        f"Use `{COMMAND_PREFIX}ROLLIT <event name>` to post a sign-up panel.\n"
        "Members click Officer Signup, Sergeant Signup, or Enlisted Signup; the organizer can re-roll at any time."
    )


@bot.command(name="adminsay", help="Privately collect a message, then post it as the bot.")
@commands.guild_only()
async def adminsay(ctx: commands.Context):
    """Delete the command, collect text in DM, then post it in this channel."""
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass

    try:
        prompt = await ctx.author.send(
            f"What should I say in **#{ctx.channel.name}**? Reply here within 2 minutes."
        )
    except discord.Forbidden:
        # Do not expose the prompt in the source channel if DMs are disabled.
        return

    def is_author_dm(message: discord.Message) -> bool:
        return message.author.id == ctx.author.id and isinstance(message.channel, discord.DMChannel)

    try:
        response = await bot.wait_for("message", check=is_author_dm, timeout=120)
    except asyncio.TimeoutError:
        await prompt.edit(content="Timed out — no message was posted.")
        return

    await ctx.channel.send(response.content, allowed_mentions=discord.AllowedMentions.none())
    await prompt.edit(content=f"Posted your message in **#{ctx.channel.name}**.")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
    bot.run(TOKEN, log_handler=handler, log_level=logging.INFO)

