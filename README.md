# Anchovies

A Slack-integrated AI team system with 16 personas, multi-project support, and production-grade safety features.

Anchovies combines a persistent Chat Hub (Marcus as coordinator) with on-demand Claude CLI work sessions in tmux. Messages flow through Slack, get routed by the Chat Hub, and when work is needed, dedicated persona tabs spawn with full file editing capabilities.

## Quick Start

```bash
# 1. Install dependencies
cd ~/paradise_brain/anchovies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your Slack credentials

# 3. Run health check
cd ~/paradise_brain && python -m anchovies.health

# 4. Start tmux session
cd ~/paradise_brain/anchovies && ./scripts/start_anchovies.sh

# 5. Start the Slack bot (separate terminal)
cd ~/paradise_brain && python -m anchovies.app
```

## Architecture

```
                              SLACK
                                |
                                v
+---------------------------------------------------------------+
|                         ASYNC SLACK BOT                        |
|  * Rate limiting (10/min/user, 30/min global)                 |
|  * Channel allowlist                                          |
|  * Input sanitisation (prompt injection detection)            |
|  * [project] tag extraction                                   |
|  * Control commands (stop/pause/resume/summary)               |
+----------+----------------------------+-----------------------+
           |                            |
      [quick chat]                [work request]
           |                            |
           v                            v
+---------------------+    +-----------------------------------+
|  CHAT HUB (Marcus)  |    |  WORK SESSION                     |
|                     |    |  (tmux tab per persona)            |
|  * Project-aware    |    |                                    |
|  * Haiku model      |    |  * Claude CLI with task prompt     |
|  * Budget tracking  |    |  * Sonnet model                   |
|  * Audit logging    |    |  * Feature branch per session     |
+---------------------+    |  * Auto-PR on completion          |
                           |  * Safety rules injected          |
                           +-----------------------------------+
```

## Features

### Team of 16 Personas
| Name | Nickname | Role | Pillar |
|------|----------|------|--------|
| Marcus | Boss | BI Manager | Leadership |
| Kai | The Optimizer | Code Quality Engineer | Leadership |
| Olivia | Scribe | Documentation Manager | Leadership |
| Elena | Pipes | Senior Data Engineer | Data Engineering |
| James | JO | Data Engineer | Data Engineering |
| Victor | Blueprint | Data Architect | Data Engineering |
| Anna | The Auditor | Data Quality Analyst | Data Engineering |
| Sofia | dbt Queen | Analytics Engineer | Analytics/Science |
| Julia | Glue | Analytics Engineer (Integration) | Analytics/Science |
| Raj | The Prophet | Data Scientist | Analytics/Science |
| Leo | Padawan | Junior Data Scientist | Analytics/Science |
| Natalie | Nat | Senior BI Analyst | Business Intelligence |
| Tom | Numbers | Data Analyst | Business Intelligence |
| Priya | P | Junior BI Analyst | Business Intelligence |
| Mike | Dashboard Mike | Reporting Analyst | Business Intelligence |
| Nina | Pixel | BI Report Designer | Business Intelligence |

### Multi-Project Support
Register projects dynamically without restarting:

```
@bot add project calculator --context ~/paradise_brain/test_calculator --desc "Test app"
@bot projects                        # List all projects
@bot set default project calculator   # Set team default
@bot [calculator] @sofia fix the multiply bug   # Project-scoped work
@bot [calculator] what's the status?            # Project-aware chat
```

Or edit `projects.yaml` directly (hot-reloaded):
```yaml
projects:
  calculator:
    display_name: "Calculator App"
    context_base: "/home/casey/paradise_brain/test_calculator"
    working_dir: "/home/casey/paradise_brain/test_calculator"
    description: "Test calculator"
default_project: calculator
```

### Safety & Git Workflow
- **Feature branches**: Each session works on `<persona>/<task-slug>`, never touches main
- **Auto-PR**: On completion, a GitHub PR is created for review (never auto-merged)
- **Protected files**: `.env`, credentials, secrets are off-limits
- **Destructive ops**: Delete/drop/migrate require Slack approval first
- **Shell allowlist**: Only approved commands (git, pytest, python, etc.)
- **Input sanitisation**: Prompt injection attempts detected and logged
- **File conflict detection**: Warns when two personas target the same file

### Cost & Resource Management
- **Model selection**: Haiku for chat (cheap), Sonnet for work (capable), per-persona overrides
- **Daily budget cap**: $25/day default, rejects new sessions when exceeded
- **Rate limiting**: 10 msgs/min per user, 30/min global
- **Max concurrent sessions**: 4 (configurable), excess requests queued
- **Session timeouts**: Soft (10 min idle) + hard (30 min total) + crash detection

### Observability
- **Structured logging**: JSON to `logs/anchovies.log` with rotation
- **Audit trail**: `python -m anchovies.audit --last 24h --member sofia`
- **Health check**: `python -m anchovies.health`
- **Watchdog**: Background task every 2 min — auto-closes dead sessions, budget warnings
- **Daily summary**: `@bot daily summary` — tasks, cost, queue status

### Bot Control
```
@bot stop all         # Kill all sessions + clear queue
@bot stop sofia       # Kill specific session
@bot pause            # Stop accepting work (chat still works)
@bot resume           # Re-enable work requests
@bot daily summary    # Today's stats
@bot projects         # List registered projects
@bot help             # Show team roster
```

### Cross-Talk (Opt-In)
Personas use `/summon <name>` to explicitly request another persona's input. Plain @mentions don't trigger — this prevents runaway chains and cost explosions. Max depth: 3.

## Project Structure

```
anchovies/
|-- app.py                      # Async Slack Bolt application
|-- config.py                   # Configuration + validation
|-- context.py                  # Persona context loader
|-- handlers.py                 # Slack event handlers + routing
|-- router.py                   # Message routing + [project] extraction
|-- cli_runner.py               # Claude CLI async subprocess runner
|-- storage.py                  # SQLite persistence (sessions, audit, budget)
|-- sanitiser.py                # Prompt injection detection
|-- git_safety.py               # Branch isolation + PR creation
|-- cost_tracking.py            # Per-call cost estimation + budget
|-- rate_limit.py               # Token-bucket rate limiter
|-- task_queue.py               # FIFO queue for excess work requests
|-- project_registry.py         # Multi-project registry + YAML I/O
|-- watchdog.py                 # Background monitoring task
|-- logging_config.py           # Structured JSON logging setup
|-- audit.py                    # Audit trail CLI
|-- health.py                   # Health check CLI
|
|-- chat_hub/
|   |-- hub.py                  # ChatHub class (Marcus coordinator)
|   |-- prompt_builder.py       # Task prompt construction + safety rules
|   +-- skill_mapper.py         # Task type -> skills mapping
|
|-- work_sessions/
|   |-- tmux_manager.py         # tmux operations (spawn, close, ready-detect)
|   |-- session_manager.py      # Session lifecycle + crash recovery
|   +-- completion.py           # Task completion flow
|
|-- slack_integration/
|   +-- poster.py               # CLI for posting to Slack
|
|-- scripts/
|   |-- start_anchovies.sh      # Start tmux session with help banner
|   |-- spawn_persona.sh        # Spawn persona tab manually
|   |-- post_to_slack.sh        # Post to Slack from shell
|   |-- slack                   # Quick slack posting alias
|   +-- help_banner.sh          # Display help in tmux
|
|-- profiles/                   # Persona YAML profiles (16)
|-- memory/                     # Persona memory files (lessons learned)
|-- status/                     # Persona general status files
|-- data/                       # SQLite database (gitignored)
|-- logs/                       # JSON log files (gitignored)
|-- rules/                      # Learned rules (future)
|-- tests/                      # 528 tests
|-- projects.yaml               # Multi-project registry
|-- RULES.md                    # 24 operational rule sections
|-- MAKEOVER.md                 # Production readiness analysis
|-- TESTING_GUIDE.md            # Manual testing procedures
+-- .env                        # Slack credentials (gitignored)
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SLACK_BOT_TOKEN` | Yes | — | Bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes | — | Socket Mode token (`xapp-...`) |
| `SLACK_SIGNING_SECRET` | Yes | — | Signing secret |
| `SLACK_CHANNEL_ID` | No | — | Default channel for posting |
| `SLACK_STATUS_CHANNEL` | No | — | Channel for watchdog alerts |
| `ALLOWED_CHANNELS` | No | (all) | Comma-separated channel IDs |
| `PROJECT_NAME` | No | "the current project" | Team/project name in prompts |
| `CHAT_MODEL` | No | haiku | Model for chat responses |
| `WORK_MODEL` | No | sonnet | Model for work sessions |
| `MAX_CONCURRENT_SESSIONS` | No | 4 | Max parallel work sessions |
| `DAILY_BUDGET` | No | 25.0 | Daily API cost cap (USD) |
| `SESSION_TIMEOUT_MINUTES` | No | 10 | Soft timeout (idle) |
| `HARD_TIMEOUT_MINUTES` | No | 30 | Hard timeout (total) |
| `MAX_SUMMON_DEPTH` | No | 3 | Cross-talk chain limit |
| `LOG_LEVEL` | No | INFO | Logging level |
| `WATCHDOG_INTERVAL_SECONDS` | No | 120 | Watchdog check interval |

## Testing

```bash
cd ~/paradise_brain/anchovies
python -m pytest tests/ -v          # Run all 528 tests
python -m pytest tests/ -x          # Stop on first failure
python -m pytest tests/test_hub.py  # Run specific test file
```

## Key Documents

- **[RULES.md](RULES.md)** — 24 operational rule sections governing all bot behaviour
- **[MAKEOVER.md](MAKEOVER.md)** — Production readiness analysis and decision record
- **[TESTING_GUIDE.md](TESTING_GUIDE.md)** — Manual testing procedures

## License

MIT
