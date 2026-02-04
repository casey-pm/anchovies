# Anchovies 🐟

A hybrid Slack-integrated chat system for AI team collaboration.

Anchovies combines a persistent Chat Hub (Marcus as coordinator) with on-demand work sessions for file editing tasks. Messages flow through Slack, get routed by the Chat Hub, and when work is needed, dedicated persona tabs spawn in tmux with full Claude CLI capabilities.

## Architecture

```
                              SLACK
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                         SLACK BOT                                │
│  • Monitors @mentions and DMs                                    │
│  • Detects: quick chat vs work request                          │
│  • Routes to Chat Hub or spawns work sessions                   │
└────────────┬─────────────────────────────┬──────────────────────┘
             │                             │
        [quick chat]                  [work request]
             │                             │
             ▼                             ▼
┌─────────────────────────┐    ┌──────────────────────────────────┐
│  CHAT HUB (Marcus)      │    │  WORK SESSION                    │
│                         │    │  (tmux tab per persona)          │
│  • Quick responses      │    │                                  │
│  • Task coordination    │    │  • Claude CLI with task prompt   │
│  • Route decisions      │    │  • File editing capabilities     │
└─────────────────────────┘    │  • Completion flow to Slack      │
                               └──────────────────────────────────┘
```

## Features

### Chat Hub
- **Marcus as Coordinator**: All messages route through Marcus first
- **Smart Routing**: Detects work requests vs quick chat automatically
- **Team of 16**: Each persona has unique profile, skills, and communication style

### Work Sessions
- **On-demand tmux tabs**: Spawn when file editing is needed
- **Task-specific prompts**: Include identity, task, files, skills, completion instructions
- **Session tracking**: Monitor active sessions, timeouts, completion status

### Slack Integration
- **Bi-directional**: Receive messages, post responses and completions
- **Thread-aware**: Work session completions reply to the original thread
- **Member identity**: Posts include persona name formatting

## Quick Start

### Prerequisites
- Python 3.10+
- [Claude CLI](https://github.com/anthropics/claude-cli) installed and authenticated
- Slack App with Socket Mode enabled
- tmux

### Installation

```bash
# Clone the repo
git clone https://github.com/yourusername/anchovies.git
cd anchovies

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Slack credentials
```

### Slack App Setup

1. Create a new app at [api.slack.com/apps](https://api.slack.com/apps)
2. Enable **Socket Mode** (Settings → Socket Mode)
3. Add **Bot Token Scopes** (OAuth & Permissions):
   - `app_mentions:read`
   - `channels:history`
   - `channels:read`
   - `chat:write`
   - `im:history`
   - `im:read`
   - `im:write`
   - `users:read`
4. **Event Subscriptions** → Subscribe to:
   - `app_mention`
   - `message.im`
   - `message.groups`
5. Install to workspace and copy tokens to `.env`

### Running

```bash
# Terminal 1: Start tmux session
./scripts/start_anchovies.sh

# Terminal 2 (or in tmux chat pane): Start Slack bot
python -m anchovies.app
```

### Testing

In Slack:
```
@bot what's the project status?          → Marcus responds (quick chat)
@bot sofia fix the bug in processor.py   → Sofia's work tab spawns in tmux
@bot @julia update your status file      → Julia's work tab spawns
```

## Project Structure

```
anchovies/
├── app.py                    # Slack Bolt application
├── config.py                 # Configuration and environment
├── handlers.py               # Slack event handlers + Chat Hub routing
├── router.py                 # Message routing logic
├── context.py                # Persona context loader
├── cli_runner.py             # Claude CLI subprocess runner
├── messages.py               # Slack Block Kit message builders
│
├── chat_hub/                 # Chat Hub module
│   ├── hub.py                # ChatHub class (Marcus coordinator)
│   ├── prompt_builder.py     # Task prompt construction
│   └── skill_mapper.py       # Task type → skills mapping
│
├── work_sessions/            # Work session management
│   ├── tmux_manager.py       # tmux operations (spawn, close tabs)
│   ├── session_manager.py    # Session lifecycle tracking
│   └── completion.py         # Task completion flow
│
├── slack_integration/        # Slack posting tools
│   └── poster.py             # CLI and API for posting to Slack
│
├── scripts/                  # Shell scripts
│   ├── start_anchovies.sh    # Start tmux session
│   ├── spawn_persona.sh      # Manually spawn persona tab
│   ├── slack                 # Quick slack posting command
│   └── post_to_slack.sh      # Full slack posting script
│
├── profiles/                 # Persona YAML profiles
│   ├── profile_marcus.yaml
│   ├── profile_sofia.yaml
│   └── ...
│
├── memory/                   # Persona memory files
├── status/                   # Persona status files
└── tmp/                      # Temporary prompt files
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_BOT_TOKEN` | Yes | Bot token (xoxb-...) |
| `SLACK_APP_TOKEN` | Yes | App token for Socket Mode (xapp-...) |
| `SLACK_SIGNING_SECRET` | Yes | Signing secret |
| `SLACK_CHANNEL_ID` | No | Default channel for posting |
| `CONTEXT_BASE` | No | Path to context files |
| `PROFILES_DIR` | No | Path to persona profiles |
| `CLAUDE_CLI_PATH` | No | Claude CLI executable (default: `claude`) |
| `DEFAULT_MEMBER` | No | Default persona (default: `marcus`) |
| `TMUX_SESSION_NAME` | No | tmux session name (default: `anchovies`) |
| `SESSION_TIMEOUT_MINUTES` | No | Auto-close timeout (default: `10`) |

## Usage

### tmux Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+b 0` | Switch to chat tab |
| `Ctrl+b n` | Next tab |
| `Ctrl+b p` | Previous tab |
| `Ctrl+b &` | Kill current tab |
| `Ctrl+b d` | Detach session |

### Work Session Completion

When a persona finishes their task:

1. **Update status file**:
   ```bash
   # Edit the status file with summary
   vim ~/path/to/status/status_<persona>.md
   ```

2. **Post to Slack**:
   ```bash
   ~/anchovies/scripts/slack "Summary of work done" --member <persona> --thread <thread_ts>
   ```

3. **Close session**: Tell the user you're done, they close with `Ctrl+b &`

### Python API

```python
# Post to Slack programmatically
from anchovies.slack_integration import post_to_slack, post_completion_message

post_to_slack("Hello world", member="sofia")
post_completion_message("sofia", "Fixed the data processor bug")

# Check active sessions
from anchovies.work_sessions import get_session_manager

mgr = get_session_manager()
print(mgr.get_status_summary())
```

## Team Members

| Name | Nickname | Role |
|------|----------|------|
| Marcus | Boss | BI Manager (Coordinator) |
| Sofia | dbt Queen | Analytics Engineer |
| Raj | The Prophet | Data Scientist |
| Leo | Padawan | Junior Data Scientist |
| Elena | Pipes | Senior Data Engineer |
| James | Jo | Data Engineer |
| Victor | Blueprint | Data Architect |
| Anna | The Auditor | Data Quality Analyst |
| Natalie | Nat | Senior BI Developer |
| Tom | Numbers | BI Analyst |
| Priya | P | Junior BI Developer |
| Mike | Dashboard Mike | Reporting Specialist |
| Nina | Pixel | Data Visualization |
| Julia | Glue | Integration Specialist |
| Olivia | Scribe | Documentation Lead |
| Kai | The Optimizer | Code Quality Lead |

## Work Request Detection

The following patterns trigger work sessions (vs quick chat):

- **Fix/Debug**: `fix bug`, `debug error`, `resolve issue`
- **Edit/Update**: `edit file`, `update status`, `modify code`
- **Create/Add**: `create function`, `add feature`, `write test`
- **Refactor**: `refactor`, `restructure`, `reorganize`
- **Git**: `commit`, `review PR`
- **File extensions**: `.py`, `.js`, `.ts`, `.css`, `.md`, `.yaml`, `.json`

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

Built with Claude CLI and Slack Bolt.
