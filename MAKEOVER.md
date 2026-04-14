# Anchovies Makeover

*Analysis date: 2026-04-14*
*Status: Plan approved, implementation in progress*

---

## 1. What Is Anchovies

A Slack-integrated AI team collaboration system with 16 personas. Two modes:

- **Chat Hub** — Marcus coordinates, answers quick questions, routes work requests
- **Work Sessions** — On-demand tmux tabs with Claude CLI, one per persona, full file system access

Flow: Slack message -> classify (chat vs work) -> Chat Hub response or tmux session spawn.

---

## 2. Production Readiness Assessment

**Verdict: Not production ready.** Good architecture, clean separation, but critical gaps in reliability, safety, and observability.

### What Works

| Area | Status |
|------|--------|
| Architecture | Clean separation: routing, chat hub, work sessions, profiles |
| Message routing | @mentions, names, aliases, broadcast, default to Marcus |
| Task classification | 12 task types with skill mapping |
| Persona system | 16 profiles with personality, memory, status, expertise |
| Slack integration | Bi-directional, thread-aware, Block Kit formatting |

### What's Broken

| ID | Issue | Severity | Location |
|----|-------|----------|----------|
| C1 | All state in-memory — restart = total amnesia | Critical | `handlers.py`, `session_manager.py` |
| C2 | JSON injection in post_to_slack.sh | Critical | `scripts/post_to_slack.sh:84-99` |
| C3 | asyncio.run() in sync handlers — concurrent messages break | Critical | `app.py:63,97` |
| C4 | Hardcoded 18s sleep — no Claude startup verification | Critical | `tmux_manager.py:179`, `spawn_persona.sh:113` |
| C5 | Zero tests | Critical | entire project |
| C6 | Unbounded memory growth | Critical | `handlers.py` (conversation_store) |
| S1 | Work request regex over-eager | Significant | `prompt_builder.py:18-45` |
| S2 | No rate limiting or max concurrent sessions | Significant | — |
| S3 | Two duplicate Claude CLI implementations | Significant | `hub.py` (sync), `cli_runner.py` (async) |
| S4 | No input sanitisation | Significant | — |
| S5 | Completion relies on persona honour system | Significant | `completion.py` |
| S6 | No audit trail | Significant | — |
| S7 | spawn_persona.sh YAML parsing fragile | Significant | `scripts/spawn_persona.sh:77-79` |
| S8 | Config validation shallow | Significant | `config.py:84-112` |
| S9 | No graceful shutdown | Significant | `app.py` |

### What's Missing

| ID | Gap | Impact |
|----|-----|--------|
| D1 | No file locking | Two personas could edit same file |
| D2 | No git branch isolation | Can push to main |
| D3 | No review gate | Changes land directly |
| D4 | No rollback mechanism | Bad changes can't be easily undone |
| D5 | No cost visibility | No tracking of API spend |
| D6 | No adaptive learning | Corrections aren't captured |
| D7 | No unattended operation | No crash detection, no watchdog |

---

## 3. Decisions

All decisions made by Casey on 2026-04-14:

| Area | Decision |
|------|----------|
| Scope | Any project. May scale to company structure |
| Users | Just Casey and the bots |
| Slack channels | Allowlist only |
| Off-limits files | .env + credentials only |
| Destructive operations | Always ask Casey in Slack first |
| Branch naming | `<persona>/<slug>` (e.g., `sofia/fix-null-processor`) |
| Commit style | Persona decides based on task size |
| Commit messages | Name in footer: `Co-Authored-By: Sofia (Anchovies) <anchovies@local>` |
| Post-task | Auto-create PR + Slack notify. Never auto-merge. Rebase on main before merge |
| Cross-talk | Opt-in only via `/summon`. No automatic @mention triggering |
| Daily budget | $25/day |
| Chat model | Configurable. Default Haiku for chat, Sonnet for work |
| Auto-review | Kai reviews code. Other reviewers assignable for complex tasks |
| Status | Slack channel + CLI |
| Daily summary | On demand (`@bot daily summary`) |
| Shell commands | Allowlist only (git, pytest, python, dbt, ls, cat, grep, find, echo, cd, mkdir) |
| Package installs | Must propose in Slack, Casey approves |
| Stuck policy | Post to Slack + wait |
| Kill switch | `@bot stop all`, `@bot stop <name>`, `@bot pause/resume` |
| Merge conflicts | Try safe resolution, escalate if unsure |
| Auto-testing | Encouraged, not enforced |

---

## 4. Implementation Plan Summary

### Phase 0: Critical Fixes (Week 1)
- 0.1 Fix JSON injection in post_to_slack.sh (use `jq`)
- 0.2 Switch to AsyncApp (eliminate asyncio.run())
- 0.3 Replace 18s sleep with ready-detection (poll pane)
- 0.4 Fix unbounded memory growth (LRU eviction)
- 0.5 Unify Claude CLI execution (all through async cli_runner.py)

### Phase 1: Persistence & Recovery (Week 2)
- 1.1 SQLite storage layer (conversations, sessions, audit, budget tables)
- 1.2 Session crash recovery (sync with tmux on startup)
- 1.3 Graceful shutdown (save state, notify Slack)

### Phase 2: Safety & Git Workflow (Week 3)
- 2.1 Git branch isolation + auto-PR workflow
- 2.2 Input sanitisation
- 2.3 File conflict detection
- 2.4 Smarter work request detection (negative patterns, confidence scoring)
- 2.5 Channel allowlist
- 2.6 Protected files
- 2.7 Destructive operation approval
- 2.8 Enhanced config validation

### Phase 3: Testing Infrastructure (Weeks 1-2, parallel)
- 3.1 Test framework setup (pytest, fixtures, mocks)
- 3.2 Unit tests for all existing modules (10 test files)
- 3.3 Integration tests (3 end-to-end flows)
- 3.4 Smoke test script

### Phase 4: Cost & Resource Management (Week 4)
- 4.1 Max concurrent sessions (4) + task queue
- 4.2 Rate limiting (10/min/user, 30/min global)
- 4.3 Cross-talk opt-in (/summon) + $25/day budget cap
- 4.4 Session timeout hardening (soft 10min + hard 30min)
- 4.5 Configurable model per context (Haiku chat, Sonnet work)

### Phase 5: Observability (Weeks 4-5)
- 5.1 Structured JSON logging with rotation
- 5.2 Audit trail CLI
- 5.3 Health check command
- 5.4 Watchdog + Slack status channel
- 5.5 Kill switch (`stop all`, `stop <name>`, `pause/resume`)
- 5.6 On-demand daily summary

### Phase 6: Parallel Performance (Weeks 5-6)
- 6.1 Async session spawning
- 6.2 Persistent task queue with priority

### Phase 7: Auto-Review + Adaptive Learning (Weeks 6-7)
- 7.1 Kai auto-reviews code changes
- 7.2 Rule learning from corrections and encouragements

### Phase 8: Scaling Foundations (Future)
- 8.1 Multi-project support
- 8.2 Company structure (persona tiers)

---

## 5. Parallel Operation Safety

| Mechanism | Protects Against |
|-----------|-----------------|
| Max 4 concurrent sessions | Resource exhaustion |
| File conflict warnings | Two personas on same file |
| Git branch per persona | Commit conflicts |
| /summon opt-in (depth 3) | Runaway cross-talk cost |
| Rate limiting (10/min) | Message flooding |
| $25/day budget cap | Cost explosion |
| Hard timeout (30 min) | Stuck sessions |
| Watchdog (2 min checks) | Silent failures |
| Kill switch | Emergency stop |

---

## 6. Unattended Operation

### Minimum Viable: Phases 0 + 1 + 4.4 + 5.4
Crash recovery, state persistence, hard timeouts, watchdog notifications.

### Full Unattended: Add Phases 4.1-4.3 + 5.5
Session limits, budget cap, kill switch.

| Scenario | After Full Implementation |
|----------|--------------------------|
| Batch tasks at night | Queued, processed (max 4), PR + Slack per task, auto-close |
| Claude stuck in loop | Hard timeout at 30 min, watchdog at 10 min, budget cap |
| Claude CLI crashes | Watchdog detects in 2 min, notifies, cleans up |
| Bot process crashes | SQLite preserves state, auto-recovery on restart |
| Budget exceeded | New sessions rejected, Slack alert, resets at midnight |
| Emergency | `@bot stop all` from phone |

---

## 7. New Files Created by Makeover

| File | Purpose |
|------|---------|
| `storage.py` | SQLite persistence layer |
| `git_safety.py` | Branch isolation, PR workflow |
| `sanitiser.py` | Input sanitisation |
| `task_queue.py` | Async priority queue |
| `health.py` | System health checks |
| `audit.py` | Audit trail CLI |
| `learning.py` | Adaptive rule learning |
| `rules/learned_rules.yaml` | Persisted learned rules |
| `tests/conftest.py` | Test fixtures |
| `scripts/smoke_test.sh` | Pre-start verification |

---

*This document is the permanent record of the Anchovies makeover analysis and decisions. The full implementation plan with test specifications is in the Claude Code plan file.*
