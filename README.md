# GoldDrop Telegram SMS monitor

An educational Python script that watches Firebase Realtime Database messages and sends matching GoldDrop reward alerts to a Telegram chat. Use it **only with databases and messages you own or have explicit permission to access**. Messages, phone numbers, OTPs and reward codes can be sensitive.

The supplied bot logic is preserved, with syntax fixes and environment-based Telegram configuration.

## What you need

- Python 3.9 or newer and internet access.
- A Telegram bot token from [@BotFather](https://t.me/BotFather), and your own private chat ID. Start a private chat with your bot using `/start`. To find the chat ID, visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a private browser session and look for `message.chat.id`. Never share that URL or its response: it contains your token and potentially private messages. If the bot already has a webhook configured, remove it before using `getUpdates`.
- A Firebase Realtime Database you are authorized to read, with `clients` and `messages/<device-id>` nodes in the format expected by the script. Its security rules must allow the configured access.

## Run locally

From this directory, create a virtual environment and install the dependency:

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Configure your Telegram bot

Copy `.env.example` to `.env` in the same directory as the script, then edit `.env` with your real values:

```dotenv
BOT_TOKEN=YOUR_BOT_TOKEN
CHAT_ID=123456789
```

On Linux / macOS, copy it with `cp .env.example .env`; on Windows PowerShell, use `Copy-Item .env.example .env`. Keep `.env` private: it is ignored by Git. Get your token from BotFather and your chat ID as described above. Do not put real secrets in `.env.example` or GitHub source files.

Alternatively, set **both** `BOT_TOKEN` and `CHAT_ID` as environment variables on your hosting provider; these override `.env` values. Environment configuration takes priority over the script constants and JSON file. If only one value is set, the script reports an error. Restart the bot after changing configuration.

As an alternative to environment configuration, create `golddrop_telegram.json` next to the script (replace the values locally; do not commit or share them):

```json
{
  "bot_token": "YOUR_BOT_TOKEN",
  "chat_id": 123456789
}
```

Optionally create `golddrop_firebase.txt` with one authorized database URL per line. Add an auth credential after `|||` if needed, with no spaces:

```text
https://YOUR_DATABASE.firebaseio.com|||YOUR_AUTH_CREDENTIAL
```

Alternatively, after starting the bot, send `/add <url>` in the configured chat. Commands: `/help`, `/add`, `/list`, `/remove`, and `/clear`. Commands are restricted to the configured **chat**, not individual users in a group; use a private chat. Avoid sending credentials in group chats.

```bash
python "golddrop_telegram_bot (1).py"
```

Stop with Ctrl+C. Existing matching messages are marked seen on initial database scan; subsequent matches are monitored every 15 seconds. By default only reward messages are sent to Telegram, while other matching messages, including OTPs, may appear in the console. The script stores seen IDs and reward details in local files. Protect these files and your logs. A successful Telegram connection requires a valid token, chat ID, and the bot's `/start` conversation.

## GitHub and deployment

The repository contains the source code; `.gitignore` excludes the local credentials and runtime data. Never commit tokens, Firebase credentials, private SMS data, or logs. If a secret is exposed, revoke or rotate it immediately.

To publish your changes, commit and push your current branch, then open a pull request on GitHub. GitHub stores the code but **does not run this long-lived polling bot**. GitHub Pages hosts static sites, and GitHub Actions is not intended as an always-on bot host. To keep it running, use a trusted computer or a persistent Python-capable server, install dependencies as above, supply the configuration files securely, and run the script as a supervised background service. Ensure the host has persistent, access-restricted storage for the configuration and state files; otherwise settings or seen-message history can be lost on restart or redeployment. Only run one instance per bot token, because multiple long-polling instances can conflict.
