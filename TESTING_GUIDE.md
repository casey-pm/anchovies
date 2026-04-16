# Anchovies Testing Guide

Manual testing procedures for verifying Anchovies features.

---

## 1. Run the Full Test Suite

```bash
cd ~/paradise_brain/anchovies
python -m pytest tests/ -v
```

Should show 359+ tests passing.

---

## 2. Test the Slack Bot End-to-End

```bash
cd ~/paradise_brain
python -m anchovies.app
```

Then in Slack:
- `@bot what's the project status?` — Marcus should reply (uses Haiku model)
- `@bot fix the bug in app.py` — should try to spawn a work session
- Send 11 messages rapidly — the 11th should get "I'm at capacity"

---

## 3. Test tmux + Help Banner

```bash
cd ~/paradise_brain/anchovies
tmux kill-session -t anchovies 2>/dev/null
./scripts/start_anchovies.sh
```

You'll see the help banner. Then from inside tmux (or another terminal):

```bash
./scripts/spawn_persona.sh sofia
```

Watch for "Waiting for Claude to start..." then "Claude ready after Xs".

---

## 4. Inspect the SQLite Database Directly

After sending some Slack messages:

```bash
sqlite3 ~/paradise_brain/anchovies/data/anchovies.db
```

Then try:

```sql
-- See today's API spend
SELECT * FROM budget;

-- See audit trail
SELECT timestamp, event_type, member, details FROM audit_log ORDER BY timestamp DESC LIMIT 20;

-- See active/completed sessions
SELECT member, status, task_description FROM sessions;

-- See stored conversations
SELECT thread_ts, last_accessed FROM conversations ORDER BY last_accessed DESC LIMIT 10;
```

---

## 5. Test post_to_slack.sh (Special Characters)

```bash
cd ~/paradise_brain/anchovies
./scripts/post_to_slack.sh 'Fixed the "null" bug — all tests passing!'
./scripts/post_to_slack.sh 'Config: {"key": "value"}' --member sofia
```

---

## 6. Create a Fake Test Project

A minimal project to test the full Anchovies workflow end-to-end.

### Setup

```bash
mkdir ~/test_anchovies_project && cd ~/test_anchovies_project
git init -b main

cat > calculator.py << 'PYEOF'
"""A calculator with some bugs for testing."""

def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def divide(a, b):
    # BUG: no zero division check
    return a / b

def multiply(a, b):
    # BUG: wrong implementation
    return a + b
PYEOF

cat > README.md << 'MDEOF'
# Test Calculator
A simple calculator for testing Anchovies.
MDEOF

git add . && git commit -m "Initial commit with bugs"
```

### Test the Workflow

1. Start tmux: `cd ~/paradise_brain/anchovies && ./scripts/start_anchovies.sh`
2. Spawn Sofia: `./scripts/spawn_persona.sh sofia`
3. In Sofia's tab, paste:
   `Fix the multiply function in ~/test_anchovies_project/calculator.py — it's adding instead of multiplying`
4. Watch her fix it, commit to a feature branch
5. Check the branch: `cd ~/test_anchovies_project && git log --all --oneline`

Or via Slack: `@bot @sofia fix the multiply bug in ~/test_anchovies_project/calculator.py`

### What This Tests

- Persona spawning with ready-detection
- The help banner in tmux
- Git branch creation (`sofia/fix-multiply-...`)
- Commit message footer (`Co-Authored-By: Sofia (Anchovies)`)
- The safety rules in the prompt (shell allowlist, protected files, destructive ops)

---

## 7. Test Rate Limiting

Send messages quickly in Slack:
```
@bot hello 1
@bot hello 2
@bot hello 3
... (keep going quickly)
@bot hello 11
```

The 11th message within a minute should get: ":hourglass_flowing_sand: I'm at capacity right now — please try again in a moment."

---

## 8. Test Budget Tracking

After using the bot for a while, check spending:

```bash
sqlite3 ~/paradise_brain/anchovies/data/anchovies.db "SELECT * FROM budget;"
```

To simulate hitting the budget cap for testing:

```bash
sqlite3 ~/paradise_brain/anchovies/data/anchovies.db \
  "INSERT OR REPLACE INTO budget (date, total_cost, call_count) VALUES (date('now'), 25.01, 100);"
```

Then try sending a work request in Slack — it should be rejected with "Daily budget reached".

Reset after testing:

```bash
sqlite3 ~/paradise_brain/anchovies/data/anchovies.db \
  "DELETE FROM budget WHERE date = date('now');"
```

---

## 9. Test /summon Cross-Talk

Spawn two personas in tmux:

```bash
./scripts/spawn_persona.sh sofia
./scripts/spawn_persona.sh raj
```

In Sofia's tab, give her a task that requires Raj's input. She should use `/summon raj` to bring him in (not just @raj). Plain `@raj` mentions should NOT trigger Raj's response.

---

## 10. Test Graceful Shutdown

Start the bot, send some messages, then press Ctrl+C. Check:

1. Terminal shows "Graceful shutdown initiated..."
2. Slack status channel gets "Anchovies bot going offline" message
3. `sqlite3 ~/paradise_brain/anchovies/data/anchovies.db "SELECT * FROM audit_log WHERE event_type = 'bot_stopped';"` shows an entry
4. tmux sessions are still running (not killed)

---

*Last updated: 2026-04-16*
