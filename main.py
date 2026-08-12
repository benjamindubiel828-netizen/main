"""Event attendance and leadership-roll Discord bot.

Set DISCORD_TOKEN in .env, invite the bot with the Message Content intent,
and ensure the bot can read member roles, send messages, and manage messages.
"""

import io
import logging
import os
import random
import sqlite3
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

    async def _may_manage(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in {self.invoker_id, interaction.guild.owner_id if interaction.guild else 0}:
            return True
        await interaction.response.send_message("Only the event starter or server owner can use this.", ephemeral=True)
        return False

    @discord.ui.button(label="Roll the Dice", style=discord.ButtonStyle.primary, emoji="🎲")
    async def roll_dice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._may_manage(interaction):
            return
        if self.rolled:
            await interaction.response.send_message("The leadership roles have already been rolled.", ephemeral=True)
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
        button.disabled = True
        embed = roster_embed(self.roster, "Leadership selection complete")
        embed.add_field(name="🎖️ Officer", value=officer.mention, inline=False)
        embed.add_field(name="🪖 Sergeants", value="\n".join(person.mention for person in sgts), inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Attendance", style=discord.ButtonStyle.success, emoji="✅")
    async def finish_attendance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._may_manage(interaction):
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
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else roster_embed(self.roster)
        embed = embed.copy()
        embed.set_footer(text=f"Attendance recorded for {len(self.roster)} member(s)")
        await interaction.response.edit_message(embed=embed, view=self)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
db = AttendanceDatabase(DATABASE_PATH)


@bot.event
async def on_ready() -> None:
    log.info("Connected as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.command(name="ROLLIT")
@commands.guild_only()
async def rollit(ctx: commands.Context) -> None:
    assert ctx.guild is not None
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
        await ctx.reply("I could not DM your status. Enable DMs from server members and try again.", delete_after=15)
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to the .env file.")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
