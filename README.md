# BakeBot

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
