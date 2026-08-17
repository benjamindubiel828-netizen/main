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

## Attendance administration

Run `!adminpanel` to post a public interactive attendance panel in
`#bakebot-center`. Anyone who can view that channel can select a server member,
then use `+1`, `−1`, or **Set total** to update that member's completed-event
count. The panel's list refreshes after every update. Restarting the bot does not
break the panel's member selector; run `!adminpanel` again whenever you want a
new panel message.

When the **Attendance** button on an event post is used, the event post is
removed and every saved administration panel refreshes automatically. **Late
attendance** lets any member viewing the event post add themselves: it grants
the `Attendance` role and adds the clicker to the active event's roster before
attendance is finalized. The bot needs **Manage Roles** permission, and its role
must be positioned above `Attendance`, for this feature.
Finalizing attendance also removes the `Attendance` role from every member in
that event's roster.

Any member with `Attendance` may press **Roll the Dice** as many times as needed.
The event post always shows the latest selection and identifies the member who
performed the most recent roll. Each roll has a 60-second per-event cooldown.
Only members with the `Officers` role can activate the red **END EVENT** button.

## Leaderboard and status

Any server member can use `!status` to receive a private message with their
completed-event total and their current role eligibility. Use `!leaderboard` to
choose a channel, then click **Post leaderboard**, for a public standings display. That display automatically
updates whenever an event is finalized or an attendance total is changed through
the administration panel.

Recognized `!` commands are deleted immediately after the bot receives them.
