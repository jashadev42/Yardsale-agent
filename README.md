# Yardsale Agent

Autonomous coding agent that works on [Yardsale](https://github.com/jashadev42/yardsale) during Jasha's work hours (10AM–4PM daily). Reads tasks from `AGENT_TASKS.md`, clones Yardsale into a work directory, edits it through Claude, commits changes to a dated `agent/YYYY-MM-DD` branch, and reports blockers via Slack.

**This is a separate repo from Yardsale itself.** On each run the agent clones (or pulls) Yardsale into `.yardsale-workdir/`, makes edits there, and pushes commits back to GitHub for Jasha to review before merging.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in ANTHROPIC_API_KEY, SLACK_*, GITHUB_TOKEN
```

## Smoke test

```bash
# Preview what the agent would do without touching files or making API calls
# (still clones Yardsale so file paths resolve):
python agent.py --dry-run --once
```

## Run locally

```bash
python agent.py --hours 6
```

Flags:
- `--dry-run` — no file writes, no shell commands
- `--once` — process a single task then exit
- `--hours N` — wall-clock budget (default 6)
- `--cost-cap USD` — daily spend cap (default $5; also `DAILY_COST_CAP_USD` env var)
- `--skip-clone` — reuse an existing `.yardsale-workdir/` (local dev only)

## Deploy to Render

1. Push this repo to a private GitHub repo (`yardsale-agent`).
2. Render → New → Cron Job → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python agent.py --hours 6`
5. Schedule: `0 10 * * *` (10 AM daily)
6. Add env vars from `.env.example`.

## Task doc

Edit `AGENT_TASKS.md` to tell the agent what to work on. Format:

```markdown
## [HIGH] Task title
- Bullet description, file paths in `backticks` get auto-loaded as context
- Any constraints the agent must respect

## Stretch Goals
## [LOW] Low-priority fallback task
```

Priorities: `[HIGH]`, `[MED]`, `[LOW]`. Mark done as `## [x] [HIGH] ...`.

## Dashboard sync

The agent writes its state to `agent_state.json`. The `yardsale-agent-dashboard.jsx` file reads this to show tasks, messages, and spend. Two sync options:

- **File-based**: set `DASHBOARD_STATE_PATH` to a shared path the dashboard can read.
- **HTTP webhook**: set `DASHBOARD_WEBHOOK_URL` to an endpoint that accepts a JSON POST of the full state on every update.

See `DASHBOARD.md` for the state schema.

## Safety

The agent:
- Commits only to `agent/YYYY-MM-DD` branches, never `main`
- Refuses commands matching deploy, force-push, reset --hard, rm -rf /, etc.
- Stops at a $5/day cost cap by default
- Asks via Slack before touching DB schema or deploying
- Blocks writes to `.env`, `.git/`, `.venv/`, `node_modules/`

Jasha reviews every commit before merging.
