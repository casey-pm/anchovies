# Anchovies Hybrid Chat System - Implementation Plan

## Overview

A hybrid Slack-integrated chat system combining:
- **Chat Hub**: Single shared Claude session for quick chat and task coordination
- **Work Sessions**: On-demand persona tabs for file editing with minimal task-specific context
- **tmux Layout**: Chat always visible + persona tabs for parallel work

---

## Architecture Diagram

```
                              SLACK
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                         SLACK BOT                                │
│  • Monitors messages                                             │
│  • Detects: chat vs work request                                 │
│  • Routes to appropriate handler                                 │
└────────────┬─────────────────────────────────┬──────────────────┘
             │                                 │
        [chat]                            [work request]
             │                                 │
             ▼                                 ▼
┌─────────────────────────┐    ┌──────────────────────────────────┐
│  CHAT HUB               │    │  PROMPT BUILDER                  │
│  (tmux pane 0)          │    │  • Chat Hub analyzes request     │
│                         │    │  • Extracts task context         │
│  • Quick responses      │    │  • Identifies relevant files     │
│  • Cross-talk           │    │  • Determines skills needed      │
│  • Task coordination    │    │  • Structures work prompt        │
│  • Prepares work prompts│    └───────────────┬──────────────────┘
└─────────────────────────┘                    │
                                               ▼
                               ┌──────────────────────────────────┐
                               │  TMUX MANAGER                    │
                               │  • Spawns persona tab            │
                               │  • Loads task prompt             │
                               │  • Manages tab lifecycle         │
                               └───────────────┬──────────────────┘
                                               │
                                               ▼
                               ┌──────────────────────────────────┐
                               │  WORK SESSION                    │
                               │  (tmux window: persona tab)      │
                               │                                  │
                               │  • Claude CLI with task context  │
                               │  • File editing capabilities     │
                               │  • Relevant skills loaded        │
                               │  • Completion sequence on finish │
                               └──────────────────────────────────┘
```

---

## tmux Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  TMUX SESSION: anchovies                                        │
├─────────────────────────────────────────────────────────────────┤
│  Windows: [0:chat]* [1:sofia] [2:leo] [3:tom]                   │
├─────────────────────────┬───────────────────────────────────────┤
│                         │                                       │
│  PANE 0: CHAT HUB       │  PANE 1: ACTIVE PERSONA TAB          │
│  (35% width, fixed)     │  (65% width)                          │
│                         │                                       │
│  Claude CLI session     │  Switches based on selected window    │
│  - Receives Slack msgs  │  - sofia: working on data_processor  │
│  - Quick chat responses │  - leo: writing tests                │
│  - Prepares task prompts│  - tom: analyzing metrics            │
│                         │                                       │
│  ────────────────────   │  [Ctrl+b 0] = back to chat           │
│  Active: @sofia fixing  │  [Ctrl+b n/p] = next/prev tab        │
│  Queued: @leo tests     │                                       │
│                         │                                       │
└─────────────────────────┴───────────────────────────────────────┘
```

---

## File Structure

```
anchovies/
├── app.py                    # Main Slack bot application
├── config.py                 # Configuration (existing, update paths)
├── context.py                # Context loader (existing)
├── router.py                 # Message routing (existing, enhance)
├── handlers.py               # Slack event handlers (existing, enhance)
│
├── chat_hub/
│   ├── __init__.py
│   ├── hub.py                # Chat hub Claude session manager
│   ├── prompt_builder.py     # Builds task prompts for work sessions
│   └── skill_mapper.py       # Maps tasks to relevant skills
│
├── work_sessions/
│   ├── __init__.py
│   ├── session_manager.py    # Manages work session lifecycle
│   ├── tmux_manager.py       # tmux pane/window operations
│   └── completion.py         # Handles task completion sequence
│
├── slack_integration/
│   ├── __init__.py
│   ├── poster.py             # CLI command to post to Slack
│   └── attachment_handler.py # Handle Slack attachments
│
├── profiles/                 # Existing persona profiles
│   ├── profile_marcus.yaml
│   ├── profile_sofia.yaml
│   └── ...
│
├── memory/                   # Existing memory files
│   ├── memory_marcus.md
│   ├── memory_sofia.md
│   └── ...
│
├── scripts/
│   ├── start_anchovies.sh    # Start tmux session with layout
│   ├── spawn_persona.sh      # Spawn a new persona tab
│   └── post_to_slack.sh      # Post message to Slack from CLI
│
└── .env                      # Environment variables
```

---

## Component Specifications

### 1. Slack Bot (app.py) - Enhanced

**Purpose**: Receive Slack messages, detect intent, route appropriately

**Changes from current**:
- Add work request detection (file edits, code changes, bug fixes)
- Route work requests to prompt builder instead of direct CLI spawn
- Handle responses from work sessions

```python
# Pseudocode
@app.event("message")
def handle_message(event):
    text = event["text"]
    target_member = detect_target(text)

    if is_work_request(text):
        # Route to Chat Hub for prompt preparation
        task_prompt = chat_hub.prepare_task_prompt(
            member=target_member,
            request=text,
            thread_context=get_thread_context(event)
        )
        tmux_manager.spawn_persona_tab(target_member, task_prompt)
    else:
        # Quick chat - route to Chat Hub directly
        response = chat_hub.get_response(target_member, text)
        post_to_slack(response)
```

### 2. Chat Hub (chat_hub/hub.py)

**Purpose**: Single persistent Claude session for coordination and prompt building

**Responsibilities**:
- Respond to quick chat messages
- Analyze work requests
- Prepare detailed task prompts
- Track active work sessions
- Coordinate cross-talk

```python
class ChatHub:
    def __init__(self):
        self.claude_process = None  # Persistent Claude CLI session
        self.active_sessions = {}   # Track persona work sessions

    def start(self):
        """Start the Chat Hub Claude session"""
        # Start claude with coordinator system prompt
        pass

    def prepare_task_prompt(self, member: str, request: str, context: dict) -> str:
        """
        Ask Chat Hub Claude to prepare a task prompt.
        Returns structured prompt for work session.
        """
        pass

    def get_response(self, member: str, message: str) -> str:
        """Get quick chat response from Chat Hub"""
        pass
```

### 3. Prompt Builder (chat_hub/prompt_builder.py)

**Purpose**: Structure task prompts for work sessions

**Output Format**:
```markdown
# Work Session: {persona_name}

## Your Identity
You are {name} ("{nickname}"), {role}.
{brief_personality}

## Task
{task_description}

## Context
- Files to examine: {file_list}
- Related context: {relevant_context}
- Thread history: {slack_thread_summary}

## Skills Available
{mapped_skills}

## When Complete
1. Update your status file: anchovies/status/status_{name}.md
2. Post summary to Slack using: /slack "{summary}"
3. You will be prompted to close this session
```

### 4. Skill Mapper (chat_hub/skill_mapper.py)

**Purpose**: Determine which skills/commands are relevant for a task

```python
SKILL_MAPPINGS = {
    "bug_fix": [
        "/commit - commit your changes when ready",
        "pytest {test_file} - run relevant tests",
    ],
    "new_feature": [
        "/commit - commit your changes",
        "Create tests in tests/ directory",
    ],
    "data_analysis": [
        "Use pandas for data manipulation",
        "Create visualizations with matplotlib",
    ],
    "documentation": [
        "Update relevant .md files",
        "/commit - commit documentation changes",
    ],
    "refactor": [
        "/commit - commit in small increments",
        "Run full test suite before finishing",
    ],
}

def map_skills(task_type: str, task_description: str) -> list[str]:
    """Return relevant skills for the task"""
    pass
```

### 5. tmux Manager (work_sessions/tmux_manager.py)

**Purpose**: Manage tmux session, windows, and panes

```python
class TmuxManager:
    SESSION_NAME = "anchovies"

    def create_session(self):
        """Create the anchovies tmux session with layout"""
        # tmux new-session -d -s anchovies
        # Split into chat pane (left) and work pane (right)
        pass

    def spawn_persona_tab(self, member: str, task_prompt: str):
        """Create new window/tab for persona work session"""
        # Save task_prompt to temp file
        # tmux new-window -n {member} "claude --system-prompt-file {prompt_file}"
        pass

    def close_persona_tab(self, member: str):
        """Close a persona's work tab"""
        # tmux kill-window -t {member}
        pass

    def list_active_tabs(self) -> list[str]:
        """List currently open persona tabs"""
        pass
```

### 6. Session Manager (work_sessions/session_manager.py)

**Purpose**: Manage work session lifecycle

```python
class SessionManager:
    def __init__(self):
        self.active_sessions = {}

    def start_session(self, member: str, task_prompt: str):
        """Start a new work session for a persona"""
        pass

    def end_session(self, member: str, summary: str):
        """End a work session - update status, post to Slack"""
        pass

    def get_session_status(self, member: str) -> dict:
        """Get current status of a work session"""
        pass
```

### 7. Completion Handler (work_sessions/completion.py)

**Purpose**: Handle task completion sequence

```python
def complete_task(member: str, summary: str):
    """
    Completion sequence:
    1. Update status file
    2. Post to Slack
    3. Prompt to close session
    """
    # Update status_{member}.md
    update_status_file(member, summary)

    # Post to Slack
    post_to_slack(member, summary)

    # Prompt user
    response = input(f"Task complete. Close {member}'s session? (y/n): ")
    if response.lower() == 'y':
        tmux_manager.close_persona_tab(member)
```

### 8. Slack Poster (slack_integration/poster.py)

**Purpose**: CLI command to post messages to Slack from work sessions

```python
#!/usr/bin/env python3
"""
Usage: python -m anchovies.slack_integration.poster "message" [--thread THREAD_TS]

Or via shell script: /slack "message"
"""

def post_message(message: str, thread_ts: str = None):
    """Post a message to the configured Slack channel"""
    pass
```

### 9. Start Script (scripts/start_anchovies.sh)

```bash
#!/bin/bash
# Start the Anchovies hybrid chat system

SESSION="anchovies"

# Kill existing session if present
tmux kill-session -t $SESSION 2>/dev/null

# Create new session with chat hub
tmux new-session -d -s $SESSION -n "chat"

# Split window: left=chat (35%), right=work area (65%)
tmux split-window -h -p 65 -t $SESSION:chat

# Left pane (0): Start Chat Hub Claude session
tmux send-keys -t $SESSION:chat.0 "cd ~/paradise_brain/anchovies && python -m chat_hub.hub" Enter

# Right pane (1): Welcome message / ready for work sessions
tmux send-keys -t $SESSION:chat.1 "echo 'Work sessions will appear here. Use Ctrl+b n/p to switch tabs.'" Enter

# Select left pane (chat) as active
tmux select-pane -t $SESSION:chat.0

# Attach to session
tmux attach -t $SESSION
```

---

## Implementation Phases

### Phase 1: Foundation (tmux + basic structure)
1. Create directory structure
2. Implement `tmux_manager.py` - create session, spawn tabs
3. Create `start_anchovies.sh` script
4. Test: manually start tmux session with layout

### Phase 2: Chat Hub
5. Implement `chat_hub/hub.py` - persistent Claude session
6. Implement basic prompt builder (static templates first)
7. Integrate Chat Hub with Slack bot
8. Test: Slack message → Chat Hub response

### Phase 3: Work Sessions
9. Implement `session_manager.py` - start/end sessions
10. Implement skill mapper
11. Enhance prompt builder with skill loading
12. Test: Work request → persona tab spawned with task prompt

### Phase 4: Completion & Integration
13. Implement `completion.py` - status update, Slack post, close prompt
14. Implement `slack_integration/poster.py` - CLI Slack posting
15. Create `/slack` shell command alias
16. Test: Full workflow end-to-end

### Phase 5: Polish
17. Add attachment handling for Slack
18. Add session timeout/cleanup
19. Add status display in chat pane
20. Documentation and error handling

---

## Configuration Updates

### .env additions
```bash
# Existing
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...

# New
SLACK_CHANNEL_ID=C...           # Default channel for posting
TMUX_SESSION_NAME=anchovies
CHAT_HUB_MODEL=sonnet           # Model for chat hub
WORK_SESSION_MODEL=sonnet       # Model for work sessions
```

### config.py updates
```python
# Add
TMUX_SESSION_NAME = os.getenv("TMUX_SESSION_NAME", "anchovies")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
CHAT_HUB_MODEL = os.getenv("CHAT_HUB_MODEL", "sonnet")
WORK_SESSION_MODEL = os.getenv("WORK_SESSION_MODEL", "sonnet")
```

---

## Work Request Detection

Keywords/patterns that trigger work session instead of chat:

```python
WORK_PATTERNS = [
    r"fix\s+(the\s+)?(bug|error|issue)",
    r"edit\s+(the\s+)?file",
    r"create\s+(a\s+)?(new\s+)?(file|function|class|module)",
    r"update\s+(the\s+)?(code|file|function)",
    r"refactor",
    r"implement",
    r"write\s+(the\s+)?(code|function|test)",
    r"add\s+(a\s+)?(feature|function|method)",
    r"delete\s+(the\s+)?(file|function|code)",
    r"review\s+(the\s+)?(code|pr|pull request)",
    r"commit",
    r"debug",
]
```

---

## Example Workflow

```
1. USER IN SLACK:
   "@sofia fix the NoneType error in data_processor.py line 142"

2. SLACK BOT:
   - Detects: work request (matches "fix.*error")
   - Target: sofia
   - Routes to Chat Hub

3. CHAT HUB:
   - Analyzes request
   - Prepares task prompt:
     """
     # Work Session: Sofia

     ## Your Identity
     You are Sofia ("dbt Queen"), Analytics Engineer.
     Communication: collaborative, professional-casual.

     ## Task
     Fix NoneType error in data_processor.py at line 142

     ## Context
     - File: domain_360_agent/services/data_processor.py
     - Error location: line 142
     - Likely cause: variable may be None before use

     ## Skills
     - Read the file and understand the context
     - Apply null checking pattern
     - Run tests: pytest tests/test_data_processor.py
     - /commit when fix is ready

     ## When Complete
     1. Update: anchovies/status/status_sofia.md
     2. Post to Slack: /slack "summary of what you did"
     3. You'll be prompted to close this session
     """

4. TMUX MANAGER:
   - Creates new window: tmux new-window -n "sofia"
   - Starts Claude with task prompt

5. USER WORKS AS SOFIA:
   - Reads file, finds bug, fixes it
   - Runs tests
   - Uses /commit

6. COMPLETION:
   Sofia: "Done! Let me update my status and post to Slack."
   > Updates status_sofia.md
   > /slack "Fixed NoneType error - added null check on line 142, tests passing"

   System: "Task complete. Close this session? (y/n)"
   User: y

   > Tab closes
   > Chat Hub notified: "Sofia's session closed"
```

---

## Success Criteria

- [ ] tmux session starts with correct layout
- [ ] Chat Hub responds to quick chat via Slack
- [ ] Work requests spawn persona tabs
- [ ] Task prompts include relevant context and skills
- [ ] Work sessions can edit files
- [ ] Completion sequence updates status and posts to Slack
- [ ] Sessions close cleanly when prompted
- [ ] Chat pane always visible while switching work tabs
- [ ] Multiple work sessions can run in parallel

---

## Design Decisions

| Question | Decision |
|----------|----------|
| **Chat Hub persona** | Marcus (unless user specifies another persona) |
| **Session timeout** | Auto-close after 10 min inactivity, BUT only if: (1) close prompt shown, (2) status already updated |
| **Queue management** | Parallel - multiple work requests spawn multiple tabs simultaneously |
| **History** | No history by default. If needed, relevant history is added to the session's startup prompt (not loaded dynamically) |

---

## Timeout Logic

```python
# Pseudocode for auto-close
class SessionTimeout:
    TIMEOUT_MINUTES = 10

    def check_session(self, member: str):
        session = self.active_sessions[member]

        if session.inactive_for > TIMEOUT_MINUTES:
            if session.close_prompt_shown and session.status_updated:
                # Safe to auto-close
                tmux_manager.close_persona_tab(member)
                notify_chat_hub(f"{member}'s session auto-closed after inactivity")
            else:
                # Not safe - remind user
                send_reminder(f"{member}: Please update status and confirm close")
```

---

*Plan created: 2026-02-04*
*Updated: 2026-02-04 (added design decisions)*
*Directory: /home/casey/paradise_brain/anchovies*
