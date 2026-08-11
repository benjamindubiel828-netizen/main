"""BakeBot: randomly assign an Officer and Sergeant for a Discord event."""

import logging
import os
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv


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


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
    bot.run(TOKEN, log_handler=handler, log_level=logging.INFO)
