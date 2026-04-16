# Anchovies Rules

The complete set of rules governing how Anchovies routes messages, spawns sessions, builds prompts, classifies tasks, and expects personas to behave.

---

## 1. Message Routing Rules

### 1.1 Channel Behaviour
- **Allowlist only**: The bot only responds in channels explicitly listed in `ALLOWED_CHANNELS` config. Messages from other channels are silently ignored.
- **DMs always allowed**: Direct messages, group DMs, and private channels bypass the allowlist.
- **Public channels** (when allowed): The bot only responds to explicit `@mentions`. Unprompted messages are ignored.
- Bot messages and message edits/deletes are always ignored.

### 1.2 Addressing a Persona
Messages are scanned in this priority order to identify the target persona:

1. **@mention** — `@sofia`, `@marcus`, etc.
2. **Name prefix** — `Sofia:`, `Marcus,` at the start of the message.
3. **Conversational prefix** — `hey sofia`, `ask raj`, `tell james`, `yo leo`.
4. **Nickname / alias** — `Boss` -> Marcus, `dbt Queen` -> Sofia, `The Prophet` -> Raj, `Padawan` -> Leo, `Nat` -> Natalie, `Dashboard Mike` -> Mike, `The Auditor` -> Anna, `Numbers` -> Tom, `Jo` -> James, `P` -> Priya, `Pipes` -> Elena, `Glue` -> Julia, `Scribe` -> Olivia, `Pixel` -> Nina, `Blueprint` -> Victor, `The Optimizer` -> Kai.
5. **Default** — If no persona is identified, the message goes to **Marcus**.

### 1.3 Broadcast
- `@all`, `@team`, or `@everyone` sends the message to all 16 team members.
- Only explicit `@` prefixed keywords trigger broadcast — casual use of the word "team" does not.

### 1.4 Marcus as Default Gateway
- If no specific member is mentioned, Marcus handles it.
- If Marcus is mentioned (alone or among others), the message routes through the Chat Hub.
- Marcus IS the Chat Hub. Messages to Marcus always go through Chat Hub logic.

---

## 2. Chat vs Work Request Classification

Every incoming message is classified as either **quick chat** or a **work request**. This determines whether Marcus responds inline or a tmux work session is spawned.

### 2.1 Work Request Triggers
A message is a work request if it matches any of these patterns:

**Action keywords:**
- Fix / debug / resolve + bug / error / issue / problem
- Edit / modify / change / update + file / code / function / class / module / status
- Create / add / write / implement + file / function / class / module / feature / test
- Delete / remove + file / function / code / line
- Refactor / restructure / reorganize
- Commit
- Review / check + code / PR / pull request / changes
- Run / execute + tests / script
- Install / setup / configure
- Update + status

**File operation keywords:**
- Read + file / files / folder / directory
- Summarise / summarize (any summarisation task)
- Write + to / in / into
- Save + to / as / in
- Generate + file / report / summary

**File extensions (anywhere in message):**
`.py`, `.js`, `.ts`, `.css`, `.md`, `.yaml`, `.json`, `.txt`, `.sql`

**Implicit trigger:** If file paths are mentioned but no action keyword matches, it is still treated as a work request.

### 2.2 Quick Chat (Everything Else)
Status questions, general discussion, coordination, opinions, and anything that doesn't match the above patterns. Marcus responds directly via the Chat Hub.

---

## 3. Task Type Classification

Once a message is confirmed as a work request, it is classified into a task type. Classification uses regex pattern matching with a scoring system — the type with the most pattern matches wins. If nothing matches, the type defaults to `general`.

| Task Type | Trigger Patterns |
|-----------|-----------------|
| `bug_fix` | fix/debug/resolve/repair + bug/error/issue/problem/crash; "not working"; "broken"; "failing" |
| `new_feature` | create/add/implement/build/write + new/feature/function/class/module |
| `code_edit` | edit/modify/change/update/alter + file/code/function/line; change/update + to |
| `testing` | write/create/add/run + test(s); "testing"; "pytest" |
| `refactor` | refactor; restructure; reorganize; clean up |
| `documentation` | document/docs/readme; write/update/add + documentation; `.md` file extension |
| `css_styling` | css; style/styling; layout; design; `.css` file extension |
| `data_analysis` | analyze/analysis; data + analysis/explore/investigate; metric(s); statistic(s) |
| `sql_query` | sql; query; bigquery; SELECT/INSERT/UPDATE/DELETE + FROM; `.sql` file extension |
| `api_integration` | api; integrate/integration; endpoint; request |
| `git_operations` | commit; git; push; pull request; PR |
| `general` | Fallback when no patterns match |

---

## 4. Skill Assignment Rules

Each task type loads a set of instructions that are injected into the work session prompt. These tell the persona how to approach the work.

### 4.1 Per-Task-Type Skills

**bug_fix:**
- Read the relevant file(s) to understand the context
- Use `/commit` to commit changes when the fix is ready
- Run tests to verify the fix: `pytest <test_file>`
- Check for similar issues in related code

**new_feature:**
- Create new file(s) as needed
- Use `/commit` to commit changes
- Write tests for new functionality in `tests/` directory
- Update documentation if adding public APIs

**code_edit:**
- Read the file first to understand current implementation
- Make targeted edits (prefer Edit tool over full rewrites)
- Use `/commit` when changes are complete
- Test your changes

**testing:**
- Run tests with: `pytest <path>`
- Run specific test: `pytest <path>::<test_name>`
- Run with verbose output: `pytest -v <path>`
- Check test coverage if needed

**refactor:**
- Understand the current code structure first
- Make incremental changes and test frequently
- Use `/commit` after each logical change
- Ensure no functionality is broken

**documentation:**
- Read existing documentation for style consistency
- Update relevant `.md` files
- Use `/commit` for documentation changes
- Keep documentation concise and accurate

**css_styling:**
- Check existing CSS patterns in `style.css`
- Follow WeasyPrint compatibility (no `gap`, `box-shadow`, `transition`)
- Use margin-based spacing as fallback for `gap`
- Test with PDF generation if applicable

**data_analysis:**
- Use pandas for data manipulation
- Create visualisations with matplotlib/seaborn
- Document findings clearly
- Export results to appropriate format

**sql_query:**
- Always use `bi_playground` dataset for ad-hoc objects (not `reporting`)
- Verify column names with `INFORMATION_SCHEMA` before querying
- Handle NULL values explicitly in comparisons
- Test queries with `LIMIT` before running on full data

**api_integration:**
- Check API documentation first
- Handle errors and edge cases
- Implement rate limiting if needed
- Log API responses for debugging

**git_operations:**
- `/commit` — Create a commit with your changes
- Review changes with `git diff` before committing
- Write clear commit messages describing the change

**general:**
- Read relevant files to understand context
- Make changes incrementally
- Test your changes
- Use `/commit` when ready

### 4.2 Context-Sensitive Bonus Skills
Additional skills are injected based on keywords in the task description:
- If `dbt` appears: add dbt run/test commands
- If `report` appears: add report generation and PDF output commands
- If `slack` appears: add Slack integration testing commands

---

## 5. Persona Rules

### 5.1 Profile Structure
Every persona is defined in a YAML profile (`profiles/profile_<name>.yaml`) containing:
- **name** and **nickname** — Display identity
- **role** — Job title
- **avatar_emoji** — Slack emoji
- **job_summary** — Condensed job description with key skills
- **personality** — `communication_style`, `formality`, `verbosity`
- **traits** — Character traits (list)
- **speech_patterns** — Phrases the persona uses often
- **expertise** — Technical skills (list)
- **handles_topics** — Keywords this persona is best suited for
- **relationships** — Reporting line and collaborators
- **team_roster** — (Marcus only) Full 16-person roster by pillar

### 5.2 Persona Behaviour Guidelines
These are injected into every persona's system prompt:
- Respond as this team member would, maintaining their personality
- Keep responses conversational but professional
- Reference your current work and status when relevant
- Apply lessons from your memory when relevant
- If asked about something outside your expertise, `@mention` a teammate who can help
- Be helpful and collaborative

### 5.3 Context Loading
When a persona is activated, the system loads (in this order):
1. **Profile** (YAML) — identity, personality, expertise, job summary
2. **Memory** (`memory/memory_<name>.md`) — lessons learned, persistent knowledge (truncated at 2000 chars in chat mode, 1500 chars in work sessions)
3. **Status** (`status/status_<name>.md`) — current work status (truncated at 2500 chars)
4. Questions and instructions files exist but are **not loaded by default** (token efficiency).

### 5.4 The Team Roster (16 Members)

| Name | Nickname | Role | Pillar |
|------|----------|------|--------|
| Marcus | Boss | BI Manager | Leadership |
| Kai | The Optimizer | Code Quality Engineer | Leadership |
| Olivia | Scribe | Documentation Manager (PT) | Leadership |
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

---

## 6. Work Session Rules

### 6.1 Spawning
- Work sessions run as Claude CLI instances inside **tmux windows** (tabs), one per persona.
- The working directory for work sessions is `paradise_brain/` (the parent of anchovies).
- Only one active session per persona at a time. If a session already exists, new tasks are sent to the existing tab instead.
- The tmux session itself (`anchovies`) must be running before work sessions can spawn. If it is not, the user is warned.
- Claude CLI takes ~18 seconds to initialise. The system waits before pasting the prompt.

### 6.2 Session Lifecycle
1. **Start** — tmux window created, Claude CLI launched, task prompt pasted.
2. **Active** — Persona works on the task. Last-activity timestamp is tracked.
3. **Complete** — Persona follows the completion sequence (see 6.3).
4. **Close** — User closes the tab with `Ctrl+b &`, or the session auto-closes after timeout.

### 6.3 Completion Sequence (Mandatory)
When a persona finishes their task, they must:
1. **Update their status file** — Edit `status/status_<name>.md` with a summary of what was done.
2. **Post a summary to Slack** — Run `~/paradise_brain/anchovies/scripts/slack "summary" --member <name> --thread <thread_ts>`.
3. **Signal completion** — Tell the user they're done so the session can be closed.

### 6.4 Work Session Behaviour Rules
These are injected into every work session prompt:
- Focus on the task at hand
- Ask clarifying questions if needed
- Test your changes before marking complete
- Keep responses concise — you're in a work session, not a chat
- When referring to team members, use their name (e.g., "Sofia") — do NOT use `@mentions` (e.g., "@sofia")

### 6.5 Delegation Rules
A persona in a work session can delegate tasks to other personas:
- **Must use the spawn script**: `~/paradise_brain/anchovies/scripts/spawn_persona.sh <name> <prompt_file>`
- **Must provide a prompt file** — never spawn a persona without one. The prompt file should be a complete task prompt (identity, skills, task, completion instructions), not a brief instruction.
- **Never run `claude --system-prompt` directly** — always use the spawn script.
- Prompt files go in `~/paradise_brain/anchovies/tmp/prompt_<name>.txt`.

---

## 7. Chat Hub (Marcus) Rules

### 7.1 Capabilities by Mode

| Capability | Chat Mode (Slack) | Work Session Mode (tmux) |
|------------|-------------------|--------------------------|
| Read/write files | No | Yes |
| Run commands | No | Yes |
| Respond to quick chat | Yes | Yes |
| Spawn work sessions | Yes (via system) | Yes (via spawn script) |
| Coordinate team | Yes | Yes |

### 7.2 Response Style
- Be concise and action-oriented (Marcus is "the Boss")
- Use characteristic phrases: "What's the blocker?", "Who owns this?", "Let's keep this moving"
- Be honest about limitations in chat mode
- Remember conversation context — don't ask users to repeat themselves

### 7.3 Routing Decisions
- Quick chat (status, questions, coordination) — respond directly as Marcus.
- File operations (read, write, create, edit) — acknowledge and let the system spawn a work session.
- Routing to other personas — acknowledge and the system handles it.

---

## 8. Session Management Rules

### 8.1 Timeout
- Sessions are considered inactive after **10 minutes** without activity.
- Auto-close only happens if **both conditions are met**: (1) the close prompt has been shown, and (2) the status file has been updated.
- If a session is inactive but these conditions are not met, it is flagged but not closed.

### 8.2 Conversation History
- Thread history is stored in memory, keyed by Slack thread timestamp.
- Maximum **200 messages** per thread (older messages are trimmed).
- The last **10 messages** of history are included when generating responses or building task prompts.
- History is included in work session prompts at spawn time (not loaded dynamically during the session).

### 8.3 tmux Sync
- The session manager periodically syncs with actual tmux state.
- If a tab was closed externally (e.g., user killed it), the tracking record is removed.
- If an untracked tab is found (manually opened), it is logged but not interfered with.

---

## 9. Cross-Talk Rules

### 9.1 How It Works
When a persona `@mentions` another team member in their response, the system automatically triggers a response from the mentioned persona in the same Slack thread.

### 9.2 Safety Limits
- **Maximum chain depth: 10** — Cross-talk chains are capped to prevent infinite loops.
- A persona **cannot trigger themselves** — if a response mentions the same persona, that self-reference is ignored.
- There is a **0.5-second delay** between cross-talk responses and between multiple persona responses.

### 9.3 Chain Depth Tracking
- User-initiated messages reset the chain depth to 0.
- Each cross-talk hop increments the depth by 1.
- At depth > 10, the chain is silently stopped.

---

## 10. Slack Integration Rules

### 10.1 Message Formatting
- Responses use Slack Block Kit with a context block (avatar + name) and a section block (response body).
- Response text is capped at **3000 characters per block**. Longer responses are split into continuation blocks.
- A "thinking" placeholder message is posted immediately and updated with the final response.
- Error messages use a `:warning:` emoji prefix.

### 10.2 Posting from Work Sessions
- Use the script: `~/paradise_brain/anchovies/scripts/slack "message" --member <name> --thread <thread_ts>`
- Completion messages should reply to the original Slack thread.

---

## 11. Critical Operational Rules

These rules override all others and apply to every persona.

### 11.1 DataForSEO API
**NO DATAFORSEO API CALLS WITHOUT CASEY'S EXPLICIT APPROVAL.**
- Never make direct DataForSEO API calls during report generation.
- Never run `populate_*.py` scripts without approval.
- Report generation reads from BigQuery (pre-populated data) — no API calls.

### 11.2 WeasyPrint CSS Compatibility
- **Never** use `page-break-inside: avoid` or `break-inside: avoid` — always use `auto`.
- Use `page-break-before: avoid` or `page-break-after: avoid` if you need to prevent breaks.
- No `gap`, `box-shadow`, or `transition` — these are not supported.
- Use margin-based spacing as a fallback for `gap`.
- Always include `box-sizing: border-box` when using percentage widths with borders/padding.

### 11.3 Code Quality
- Always verify HTML class names in `report_generator.py` before writing CSS specs.
- CSS-only changes rarely work for visual enhancements — most require paired CSS + Python changes.
- Before marking any visual enhancement complete: generate a test report, inspect with dev tools, confirm the CSS class is present and the style is applied.
- When adding a parameter to a function: update the signature, find ALL call sites (grep), update every call site including parallel/async paths, and test both sequential and parallel code paths.
- When calculating paths with `Path.parent`: print the actual path during debugging, count directory levels carefully, verify by checking if expected files exist.

### 11.4 SQL Conventions
- Always use the `bi_playground` dataset for ad-hoc objects (not `reporting`).
- Verify column names with `INFORMATION_SCHEMA` before querying.
- Handle NULL values explicitly.
- Test with `LIMIT` before running full queries.

### 11.5 Project Management Lessons
- Identify parallel workstreams and staff them independently — don't serialise work that can run concurrently.
- Schedule modularisation when a file exceeds ~2,000 lines.
- Close DQ issues decisively: fixed, not reproducible (closed), or known issue (documented). No permanent limbo.
- Clean question files at wrap-up — move resolved items to summary tables.

---

## 12. Infrastructure Rules

### 12.1 Prerequisites
- Python 3.10+
- Claude CLI installed and authenticated
- Slack App with Socket Mode enabled
- tmux

### 12.2 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_BOT_TOKEN` | Yes | Bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes | App token for Socket Mode (`xapp-...`) |
| `SLACK_SIGNING_SECRET` | Yes | Signing secret |
| `SLACK_CHANNEL_ID` | No | Default channel for posting |
| `SLACK_STATUS_CHANNEL` | No | Channel for system notifications and alerts |
| `ALLOWED_CHANNELS` | No | Comma-separated list of channel IDs the bot responds in |
| `CONTEXT_BASE` | No | Path to context files (default: Domain_360 enhancements dir) |
| `PROFILES_DIR` | No | Path to persona profiles (default: `anchovies/profiles/`) |
| `JOB_DESCRIPTIONS_DIR` | No | Path to job descriptions (default: `my libraries/job_descriptions/`) |
| `CLAUDE_CLI_PATH` | No | Claude CLI executable (default: `claude`) |
| `CHAT_MODEL` | No | Model for chat responses (default: `haiku`) |
| `WORK_MODEL` | No | Model for work sessions (default: `sonnet`) |
| `DEFAULT_MEMBER` | No | Default persona (default: `marcus`) |
| `TMUX_SESSION_NAME` | No | tmux session name (default: `anchovies`) |
| `SESSION_TIMEOUT_MINUTES` | No | Soft timeout in minutes (default: `10`) |
| `HARD_TIMEOUT_MINUTES` | No | Hard timeout in minutes (default: `30`) |
| `MAX_CONCURRENT_SESSIONS` | No | Max parallel work sessions (default: `4`) |
| `DAILY_BUDGET` | No | Daily API cost cap in dollars (default: `25`) |
| `MAX_SUMMON_DEPTH` | No | Max cross-talk chain depth (default: `3`) |

### 12.3 CLI Execution
- Chat Hub uses `claude --print -p <prompt>` for one-shot responses (120-second timeout).
- Work sessions use interactive `claude` (no `--print` flag) inside tmux.
- If Claude CLI is not found, a `ClaudeCliError` is raised.
- Empty responses from Claude CLI are treated as errors.

---

## 13. Git & Version Control Rules

### 13.1 Branch Isolation
- Every work session operates on its own feature branch: `<persona>/<task-slug>` (e.g., `sofia/fix-null-processor`).
- The branch is created automatically before the session starts.
- Personas commit only to their feature branch.

### 13.2 Forbidden Git Operations
These are **never** allowed, under any circumstances:
- Push to `main` or `master`
- Merge to `main` or `master`
- Force push to any branch
- Rebase published/shared branches without approval

### 13.3 Commit Messages
- Commit messages should describe the change clearly.
- Every commit must include a footer: `Co-Authored-By: <Name> (Anchovies) <anchovies@local>`
- Commit frequency is at the persona's discretion: small tasks get one commit, large tasks get several atomic commits.

### 13.4 PR Workflow
- When a persona completes a task, a PR is automatically created from their feature branch.
- The PR is posted to Slack with: branch name, files changed, and diff summary.
- PRs are **never** auto-merged. Casey must review, approve, and merge.
- Before merging, the branch must be rebased on main.

---

## 14. Shell Command Rules

### 14.1 Allowed Commands
These commands may be run freely:
`git`, `pytest`, `python`, `dbt`, `ls`, `cat`, `head`, `tail`, `grep`, `find`, `echo`, `cd`, `mkdir`

### 14.2 Blocked Commands
These commands are **never** allowed:
- `rm -rf` (recursive force delete)
- `sudo` (any elevated privilege operation)
- `shutdown`, `reboot`
- `curl` or `wget` to external URLs
- `pip install`, `npm install` (see 14.3)

### 14.3 Commands Requiring Approval
These commands require Casey's explicit approval via Slack before execution:
- `pip install <package>` — persona must post the package name and reason to Slack, then wait for approval
- `npm install <package>` — same as above
- Any package manager install operation

### 14.4 When Unsure
If a persona is unsure whether a command is allowed, they must ask in Slack first. When in doubt, don't run it.

---

## 15. Protected Files

### 15.1 Off-Limits Files
Personas must **never** edit, delete, or overwrite these files:
- `.env` and `.env.*` (environment configuration)
- `credentials.*` (API keys, tokens)
- `**/secrets/**` (any file in a secrets directory)

### 15.2 Reading Is Allowed
Personas may read protected files to understand configuration, but must never modify them.

---

## 16. Destructive Operation Rules

### 16.1 Approval Required
Before performing **any** of these operations, a persona must post to Slack and **wait** for Casey's explicit approval:
- Deleting files
- Dropping database tables
- Overwriting data files
- Running migration scripts
- Any operation that cannot be easily undone

### 16.2 How to Request Approval
Post a Slack message describing: what you want to do, why, and what the impact will be. Wait for Casey's "approved" or "go ahead" response before proceeding.

---

## 17. Cost & Resource Rules

### 17.1 Daily Budget
- The daily API cost budget is **$25**.
- When the budget is reached, no new sessions are spawned and no new chat responses are generated until midnight reset.
- At 80% budget ($20), a warning is posted to Slack.

### 17.2 Concurrent Session Limit
- Maximum **4** concurrent work sessions at any time.
- Additional requests are queued and processed as sessions complete.
- Queued requests are notified in Slack with their queue position.

### 17.3 Rate Limiting
- Maximum 10 messages per minute per user.
- Maximum 30 messages per minute globally.
- Exceeded limits result in a polite "at capacity" response.

### 17.4 Model Selection
- Chat (non-work) messages use **Haiku** by default (cheaper, faster).
- Work sessions use **Sonnet** by default (more capable).
- Per-persona model overrides are configurable in profile YAML.

---

## 18. Cross-Talk Rules

### 18.1 Opt-In Only
Cross-talk is **not automatic**. A persona mentioning another persona's name (e.g., "@sofia") does **not** trigger a response from that persona.

### 18.2 The /summon Command
To explicitly request another persona's input, a persona must use `/summon <name>` in their response. This is the only way to trigger cross-talk.

### 18.3 Depth Limit
- Maximum summon chain depth: **3** hops.
- A persona cannot summon themselves.
- At the depth limit, the chain silently stops.

---

## 19. Escalation Rules

### 19.1 When Stuck
If a persona is stuck and cannot make progress:
1. Post to Slack: "I'm stuck on X because Y"
2. Pause all work on the task
3. Wait for Casey's response before continuing

Do not attempt workarounds, do not guess, do not proceed with uncertainty.

### 19.2 Merge Conflicts and Data Loss Risk
If a persona encounters a merge conflict or potential data loss:
1. Attempt a safe resolution (e.g., keep both versions, create backup)
2. If unsure about the resolution, stop and escalate to Slack immediately
3. Never discard someone else's changes without approval

### 19.3 Kill Switch
Casey can issue these commands at any time:
- `@bot stop all` — immediately kills all work sessions and clears the queue
- `@bot stop <name>` — kills a specific persona's session
- `@bot pause` — stops accepting new tasks (active sessions finish normally)
- `@bot resume` — re-enables task acceptance after a pause

---

## 20. Review Rules

### 20.1 Kai Auto-Review
After a code-changing work session completes, **Kai** (The Optimizer) automatically reviews the diff and posts findings to Slack. This does not apply to documentation or analysis tasks.

### 20.2 Custom Reviewers
For complex tasks requiring additional perspectives, Casey can assign other reviewers: `@bot review sofia's work with raj`.

### 20.3 Review Does Not Mean Approval
Kai's review (or any persona's review) is advisory only. Casey is the final approver for all PRs.

---

## 21. Monitoring & Status Rules

### 21.1 Status Channel
System notifications, warnings, and alerts are posted to a dedicated Slack status channel (`SLACK_STATUS_CHANNEL`).

### 21.2 Watchdog
A background watchdog checks every 2 minutes for:
- Sessions exceeding soft timeout (10 min idle)
- Crashed Claude CLI processes
- Completed sessions with tabs still open
- Budget warnings at 80% threshold

### 21.3 Daily Summary
Available on demand via `@bot daily summary`. Shows: tasks completed, branches ready for review, active sessions, and cost spent.

### 21.4 Health Check
`python -m anchovies.health` verifies: bot process, Slack connection, tmux session, SQLite database, Claude CLI, gh CLI, jq.

---

## 22. Adaptive Learning Rules

### 22.1 Correction Capture
When Casey corrects a persona's behaviour (using phrases like "no", "don't", "stop", "always", "never", "from now on"), the correction is saved as a persistent rule in `rules/learned_rules.yaml`.

### 22.2 Encouragement Capture
When Casey confirms a non-obvious good approach ("yes exactly", "perfect", "keep doing that"), the approach is saved as a positive rule to prevent drift.

### 22.3 Rule Injection
All learned rules are injected into future session prompts so the same mistake is not repeated and validated approaches are maintained.

### 22.4 Rule Management
Learned rules can be reviewed, edited, and removed via CLI: `python -m anchovies.rules list|add|remove`.

---

*Generated from codebase analysis on 2026-04-14.*
---

## 23. Multi-Project Rules

### 23.1 Project Registration
Projects are registered in `projects.yaml` (hot-reloaded) or via Slack commands (`@bot add project ...`). Each project has a name (slug), display name, context directory, and working directory.

### 23.2 Project Tagging
Use square brackets to specify a project in Slack messages: `[calculator] fix the multiply bug`. If no tag is given and a default project is set, the default is used. If no tag and no default, the system uses the generic team context.

### 23.3 Status File Layers
- **Persona status** (`anchovies/status/status_sofia.md`) — Sofia's general status across all projects
- **Project status** (`<project_context>/status/status_sofia.md`) — Sofia's status on a specific project

When working on a project, personas update the project's status. Their general persona status stays separate.

### 23.4 Working Directory
Each project has a `working_dir` (typically the git repo path). When a session is spawned for a project, tmux opens in that directory. Without a project, the default `paradise_brain/` parent directory is used.

### 23.5 Memory Is Project-Independent
Persona memory files (lessons learned) are NOT project-specific. A lesson learned on one project applies everywhere.

### 23.6 Slack Commands
| Command | Action |
|---------|--------|
| `@bot projects` | List all registered projects |
| `@bot add project <name> --context <path>` | Register a project |
| `@bot remove project <name>` | Unregister a project |
| `@bot set default project <name>` | Set the team default |
| `@bot clear default project` | Clear the default |
| `@bot project info <name>` | Show project details |

---

*Updated with Casey's decisions on 2026-04-14. Multi-project support added 2026-04-16.*
*Source files: app.py, config.py, context.py, router.py, handlers.py, chat_hub/hub.py, chat_hub/prompt_builder.py, chat_hub/skill_mapper.py, work_sessions/tmux_manager.py, work_sessions/session_manager.py, work_sessions/completion.py, cli_runner.py, messages.py, scripts/spawn_persona.sh, profiles/*.yaml, memory/*.md*
