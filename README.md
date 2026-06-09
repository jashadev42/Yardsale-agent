# Yardsale Agent

An autonomous coding agent that works on [Yardsale](https://github.com/jashadev42/yardsale). It picks tasks from a Markdown file, clones the repo, edits code through Claude, commits to a dated branch, and reports progress over Slack — all without human input until review time.

**Stack:** Python · Claude API (claude-sonnet-4-6) · Slack API · GitHub API · Render (cron)

---

## How it works

```
AGENT_TASKS.md          Slack (!task …)
       │                      │
       └──────────┬───────────┘
                  ▼
           pick_next_task()
                  │
                  ▼
    Clone / pull Yardsale repo
                  │
                  ▼
       ┌─── Claude agentic loop ────────────────────┐
       │  read_file → list_files → write_file        │
       │  run_shell → ask_user → mark_complete       │
       │                                             │
       │  Each write_file call auto-commits and      │
       │  pushes to agent/YYYY-MM-DD branch          │
       └─────────────────────────────────────────────┘
                  │
                  ▼
        Slack: ✅ task summary  /  ⚠️ blocker question
                  │
                  ▼
        Dashboard state → GitHub Gist (polled by UI)
```

The main loop runs for a configurable number of hours, cycling through tasks until it exhausts the task list, hits the daily cost cap, or the time budget expires.

## Key design decisions

**Branch isolation** — every commit lands on `agent/YYYY-MM-DD`, never `main`. The agent is structurally incapable of touching the production branch.

**Per-write auto-commit** — `write_file` stages, commits, and pushes automatically after each file change. Claude sees the result in the tool response and never has to manage git manually, which eliminates duplicate-commit bugs.

**Bidirectional Slack integration** — the agent posts blockers and completions as Slack messages, and polls the channel for `!task <description>` messages to inject new work into the queue at runtime.

**Cost cap** — a configurable daily USD limit (`$5` default) is enforced before every Claude API call. If the cap is hit the agent notifies Slack and sleeps until the day resets.

**Safety deny-list** — `run_shell` rejects commands matching patterns like `git push ... main`, `git reset --hard`, `rm -rf /`, `render deploy`, and others before executing anything.

**Prompt caching** — the system prompt is sent with `cache_control: ephemeral` to reduce token cost on the repeated Claude calls within a single task's agentic loop.

**Dashboard state sync** — after every state change, `agent_state.json` is PATCHed to a secret GitHub Gist so a hosted dashboard can poll it without any backend.

## Project structure

```
agent.py              # entire agent: task parsing, Claude loop, tool implementations
AGENT_TASKS.md        # task queue (edit this to queue work)
agent_state.json      # live state written on every update
dashboard/            # React dashboard component that reads agent_state.json
requirements.txt
.env.example
DASHBOARD.md          # state schema reference
```

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in ANTHROPIC_API_KEY, SLACK_*, GITHUB_TOKEN
```

## Run

```bash
# Dry run — no file writes, no API calls (still clones repo so paths resolve)
python agent.py --dry-run --once

# Normal run — work for up to 6 hours
python agent.py --hours 6
```

Flags:
- `--dry-run` — preview without writing files or running shell commands
- `--once` — process a single task then exit
- `--hours N` — wall-clock budget (default `6`)
- `--cost-cap USD` — daily spend cap (default `$5`; also `DAILY_COST_CAP_USD` env var)
- `--skip-clone` — reuse an existing `.yardsale-workdir/` for local iteration

## Task format

Edit `AGENT_TASKS.md` to queue work:

```markdown
## [HIGH] Task title
- Description, constraints, and any `file/paths.py` the agent should preload

## Stretch Goals
## [LOW] Low-priority fallback task
```

Priorities: `[HIGH]` → `[MED]` → `[LOW]`. Mark done with `## [x] [HIGH] …`. Tasks can also be injected at runtime via Slack: `!task fix the broken search filter`.

## Deploy to Render

1. Push this repo to GitHub.
2. Render → **New → Cron Job** → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python agent.py --hours 6`
5. Schedule: `0 10 * * *`
6. Add env vars from `.env.example`.

## Dashboard

`agent_state.json` is synced to a GitHub Gist on every update. The React component in `dashboard/` polls the raw Gist URL and renders current task, queue, spend, and message history. See `DASHBOARD.md` for the full state schema.

## Safety summary

| Guardrail | Mechanism |
|---|---|
| Never touches `main` | `ensure_agent_branch()` always checks out `agent/YYYY-MM-DD` |
| No destructive shell commands | Regex deny-list checked before every `subprocess.run` |
| No secrets overwritten | `FORBIDDEN_PATHS` / `FORBIDDEN_PREFIXES` checked on every write |
| Schema changes require approval | System prompt instructs `ask_user` before any DB/migration work |
| Spend limit | USD cap enforced before every Claude API call |
