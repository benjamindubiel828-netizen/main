# BakeBot

Discord event attendance and leadership-roll bot.

## Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Put `DISCORD_TOKEN=your-token` in `.env`.
3. In the Discord Developer Portal, enable the **Message Content** and
   **Server Members** privileged gateway intents for the bot.
4. Invite the bot with permissions to View Channels, Send Messages, Embed Links,
   Read Message History, and Manage Messages (for silent `!status`).
5. Start it with `python main.py`.

The role names must be exactly `Officers`, `Non-Commission Officer`, `Enlisted`,
and `Attendance`. `!ROLLIT` posts the current Attendance roster, then allows its
invoker or the server owner to roll one Officer and two unique Sergeants.
`!status` deletes the command and DMs the caller their completed-event total and
their current eligibility. The SQLite database is saved as `attendance.sqlite3`.
