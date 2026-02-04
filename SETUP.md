# Anchovies Setup Guide

This guide walks you through setting up Anchovies from scratch.

## Prerequisites

### 1. Python 3.10+

```bash
python --version  # Should be 3.10 or higher
```

### 2. Claude CLI

Install and authenticate the Claude CLI:

```bash
# Install (if not already installed)
npm install -g @anthropic-ai/claude-cli

# Authenticate
claude auth
```

Verify it works:
```bash
claude --version
claude -p "Hello" --print
```

### 3. tmux

```bash
# Ubuntu/Debian
sudo apt install tmux

# macOS
brew install tmux

# Verify
tmux -V
```

### 4. Slack App

See [Slack App Setup](#slack-app-setup) below.

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/anchovies.git
cd anchovies
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate     # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and add your Slack credentials (see below).

---

## Slack App Setup

### 1. Create the App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From scratch**
3. Name: `Anchovies` (or your choice)
4. Workspace: Select your workspace

### 2. Enable Socket Mode

1. Go to **Settings** → **Socket Mode**
2. Toggle **Enable Socket Mode** ON
3. Create an App-Level Token:
   - Name: `anchovies-socket`
   - Scope: `connections:write`
4. Copy the token (starts with `xapp-`) to `.env` as `SLACK_APP_TOKEN`

### 3. Add Bot Permissions

1. Go to **OAuth & Permissions**
2. Under **Scopes** → **Bot Token Scopes**, add:

| Scope | Purpose |
|-------|---------|
| `app_mentions:read` | Receive @mentions |
| `channels:history` | Read channel messages |
| `channels:read` | List channels |
| `chat:write` | Post messages |
| `im:history` | Read DM history |
| `im:read` | Access DMs |
| `im:write` | Send DMs |
| `groups:history` | Read private channel history |
| `groups:read` | Access private channels |
| `users:read` | Get user info |

### 4. Enable Events

1. Go to **Event Subscriptions**
2. Toggle **Enable Events** ON
3. Under **Subscribe to bot events**, add:
   - `app_mention`
   - `message.im`
   - `message.groups`

### 5. Install to Workspace

1. Go to **OAuth & Permissions**
2. Click **Install to Workspace**
3. Authorize the app
4. Copy **Bot User OAuth Token** (starts with `xoxb-`) to `.env` as `SLACK_BOT_TOKEN`

### 6. Get Signing Secret

1. Go to **Basic Information**
2. Under **App Credentials**, find **Signing Secret**
3. Copy to `.env` as `SLACK_SIGNING_SECRET`

### 7. Get Channel ID (Optional)

For the default posting channel:
1. In Slack, right-click the channel → **View channel details**
2. Scroll down to find the Channel ID (starts with `C`)
3. Add to `.env` as `SLACK_CHANNEL_ID`

---

## Directory Setup

### Context Files

Anchovies expects these directories for persona context:

```bash
# Create if they don't exist
mkdir -p status memory tmp
```

### Profiles

The `profiles/` directory should contain YAML files for each persona:

```yaml
# profiles/profile_sofia.yaml
name: "Sofia"
nickname: "dbt Queen"
role: "Analytics Engineer"
avatar_emoji: ":woman_technologist:"

personality:
  communication_style: "collaborative"
  formality: "professional-casual"
  verbosity: "moderate"

traits:
  - "Detail-oriented"
  - "Helpful and patient"

expertise:
  - "dbt"
  - "SQL"
  - "Data modeling"
```

---

## Running Anchovies

### 1. Start tmux Session

```bash
./scripts/start_anchovies.sh
```

This creates the `anchovies` tmux session with the chat tab.

### 2. Start Slack Bot

In the tmux chat pane (or another terminal):

```bash
source venv/bin/activate  # If using venv
python -m anchovies.app
```

You should see:
```
STARTING BI TEAM FORUM BOT
Bot Token: xoxb-...
Connecting to Slack via Socket Mode...
Bot is running! Press Ctrl+C to stop.
```

### 3. Test in Slack

Send a message in a channel where the bot is present:
```
@Anchovies hello
```

Marcus should respond!

---

## Troubleshooting

### "SLACK_BOT_TOKEN is not set"

Make sure your `.env` file exists and contains valid tokens:
```bash
cat .env | grep SLACK
```

### "Claude CLI not found"

Ensure Claude CLI is installed and in your PATH:
```bash
which claude
claude --version
```

### Bot doesn't respond

1. Check the bot is running (no errors in terminal)
2. Verify the bot was invited to the channel
3. Check Slack app event subscriptions are enabled

### tmux "size missing" error

Run from a proper terminal (not VS Code integrated terminal):
```bash
# In Windows Terminal / WSL
./scripts/start_anchovies.sh
```

### Work session tab doesn't show prompt

Increase the wait time in `work_sessions/tmux_manager.py`:
```python
time.sleep(10)  # Increase from 8 if needed
```

---

## Updating

```bash
git pull origin main
pip install -r requirements.txt --upgrade
```

Then restart the bot.
