# BakeBot

<<<<<<< HEAD
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
=======
A Discord bot that collects Officer and Sergeant volunteers, then randomly selects one person for each role.

## Setup

1. Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. On the bot's **Privileged Gateway Intents** page, enable **Message Content Intent** and **Server Members Intent**.
3. Invite the bot to your server with the `bot` scope and permissions to View Channels, Send Messages, and Embed Links.
4. Create a `.env` file beside `main.py`:

   ```env
   DISCORD_TOKEN=your-bot-token-here
   # COMMAND_PREFIX=!
   ```

5. Install and run:

   ```powershell
   py -m pip install -r requirements.txt
   py main.py
   ```

## Commands

```
!ROLLIT <event name>
```

For example:

```
!ROLLIT Friday patrol
```

The bot posts an Apollo-style sign-up panel with **Officer Signup**, **Sergeant Signup**, and **Enlisted Signup** buttons. Only members with the **Officers** role can use Officer Signup. Members with either **Officers** or **Non-Commission Officer** can use Sergeant Signup. Anyone can use Enlisted Signup. Enlisted attendees are listed on the panel but are not included in the leadership draw.

The event organizer (or a server member with **Manage Server**) uses the panel's **Randomize Assignments** button to choose one distinct Officer and Sergeant from the matching sign-up pools.

The event stays open after each draw: additional members can sign up, and **Re-Roll** picks again from the current signup lists. **Delete Event** removes the event panel; only its organizer or a server member with **Manage Server** can use it.

Use `!assignhelp` for a reminder in Discord. Signups are held in memory, so create a new event panel if the bot restarts before you randomize.
>>>>>>> cefd4a0e58f67f4705c2aa2ffaaa6475a91e5737
