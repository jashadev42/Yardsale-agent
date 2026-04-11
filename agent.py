"""Yardsale autonomous coding agent.

Runs during Jasha's work hours, picks tasks from AGENT_TASKS.md, clones the
Yardsale repo into a work directory, edits it through Claude, commits changes
to a dedicated branch, and reports blockers to Slack. Accepts new tasks from
Slack messages beginning with `!task`.

Entry point: `python agent.py` (add `--dry-run` to preview without writing).

This repo is *separate* from Yardsale itself. On each run the agent clones
(or pulls) Yardsale into WORK_DIR, makes edits there, and pushes commits back
to a safe branch for Jasha to review before merging.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

AGENT_ROOT = Path(__file__).resolve().parent
TASKS_FILE = AGENT_ROOT / "AGENT_TASKS.md"
STATE_FILE = AGENT_ROOT / "agent_state.json"
LOG_FILE = AGENT_ROOT / "agent_log.txt"

# Where we clone Yardsale for editing. Override with YARDSALE_WORK_DIR.
DEFAULT_WORK_DIR = Path(os.getenv("YARDSALE_WORK_DIR", str(AGENT_ROOT / ".yardsale-workdir")))
REPO_URL = os.getenv("YARDSALE_REPO_URL", "https://github.com/jashadev42/yardsale.git")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # PAT with repo scope; required for push

AGENT_BRANCH_PREFIX = "agent/"  # agent work goes to agent/YYYY-MM-DD branches

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000
MAX_TOOL_ITERATIONS = 25
SLACK_POLL_INTERVAL_SEC = 180
DEFAULT_RUN_HOURS = 6.0
DEFAULT_DAILY_COST_CAP_USD = 5.0

# Files and directories the agent must never touch inside Yardsale.
FORBIDDEN_PATHS = {
    ".env",
    ".env.local",
    ".env.production",
}
FORBIDDEN_PREFIXES = (".git/", ".venv/", "node_modules/")


# ---------------------------------------------------------------------------
# Runtime configuration — set once `prepare_work_dir()` runs.
# ---------------------------------------------------------------------------


class RunContext:
    """Holds the current working copy of Yardsale for tool calls to reference."""

    work_dir: Path = DEFAULT_WORK_DIR


# ---------------------------------------------------------------------------
# Logging and state
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def log(msg: str) -> None:
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


def default_state() -> dict[str, Any]:
    return {
        "current_task": None,
        "status": "idle",
        "queue": [],
        "completed_today": [],
        "blocked_reason": None,
        "blocked_question": None,
        "last_slack_poll_ts": "0",
        "last_updated": now_iso(),
        "cost_day": today_key(),
        "cost_today_usd": 0.0,
    }


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return default_state()
    try:
        state = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        log("agent_state.json corrupted — resetting")
        return default_state()
    # Reset daily cost counter at UTC day boundary.
    if state.get("cost_day") != today_key():
        state["cost_day"] = today_key()
        state["cost_today_usd"] = 0.0
        state["completed_today"] = []
    return state


def save_state(state: dict[str, Any]) -> None:
    state["last_updated"] = now_iso()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    publish_dashboard_state(state)


# ---------------------------------------------------------------------------
# Dashboard sync — writes a copy of state to DASHBOARD_STATE_PATH if set,
# or POSTs to DASHBOARD_WEBHOOK_URL. See DASHBOARD.md in this repo.
# ---------------------------------------------------------------------------


def publish_dashboard_state(state: dict[str, Any]) -> None:
    target = os.getenv("DASHBOARD_STATE_PATH")
    if target:
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(json.dumps(state, indent=2))
        except OSError as exc:
            log(f"dashboard state write failed: {exc}")

    webhook = os.getenv("DASHBOARD_WEBHOOK_URL")
    if webhook:
        try:
            requests.post(webhook, json=state, timeout=5)
        except requests.RequestException as exc:
            log(f"dashboard webhook failed: {exc}")

    gist_id = os.getenv("DASHBOARD_GIST_ID")
    if gist_id and GITHUB_TOKEN:
        try:
            requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers={
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github+json",
                },
                json={"files": {"agent_state.json": {"content": json.dumps(state, indent=2)}}},
                timeout=10,
            )
        except requests.RequestException as exc:
            log(f"dashboard gist sync failed: {exc}")


# ---------------------------------------------------------------------------
# Git / work-dir management
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def _authenticated_repo_url() -> str:
    """Inject the GitHub token into the clone URL for push auth."""
    if not GITHUB_TOKEN:
        return REPO_URL
    if REPO_URL.startswith("https://"):
        return REPO_URL.replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@", 1)
    return REPO_URL


def prepare_work_dir() -> Path:
    """Clone Yardsale into WORK_DIR (or pull if it already exists)."""
    work_dir = DEFAULT_WORK_DIR
    RunContext.work_dir = work_dir
    auth_url = _authenticated_repo_url()

    if (work_dir / ".git").exists():
        log(f"work dir exists — pulling latest in {work_dir}")
        _git(["fetch", "origin"], work_dir)
        _git(["checkout", "main"], work_dir)
        _git(["reset", "--hard", "origin/main"], work_dir)
        _git(["clean", "-fdx"], work_dir)
        return work_dir

    work_dir.parent.mkdir(parents=True, exist_ok=True)
    log(f"cloning {REPO_URL} into {work_dir}")
    subprocess.run(
        ["git", "clone", "--depth", "50", auth_url, str(work_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    # Configure commit identity for Render's ephemeral environment.
    _git(["config", "user.email", os.getenv("GIT_USER_EMAIL", "agent@yardsale.local")], work_dir)
    _git(["config", "user.name", os.getenv("GIT_USER_NAME", "Yardsale Agent")], work_dir)
    return work_dir


def ensure_agent_branch(work_dir: Path) -> str:
    """Create or reuse today's agent branch so commits stay isolated from main."""
    branch = f"{AGENT_BRANCH_PREFIX}{today_key()}"
    existing = _git(["branch", "--list", branch], work_dir).stdout.strip()
    if existing:
        _git(["checkout", branch], work_dir)
    else:
        _git(["checkout", "-b", branch], work_dir)
    return branch


def push_agent_branch(work_dir: Path, branch: str) -> bool:
    if not GITHUB_TOKEN:
        log("GITHUB_TOKEN not set — skipping push")
        return False
    result = _git(["push", "-u", "origin", branch], work_dir, check=False)
    if result.returncode != 0:
        log(f"push failed: {result.stderr.strip()}")
        return False
    log(f"pushed {branch} to origin")
    return True


# ---------------------------------------------------------------------------
# Task parsing
# ---------------------------------------------------------------------------


PRIORITY_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}
TASK_HEADING_RE = re.compile(r"^##\s*(?:\[(x)\]\s*)?\[(HIGH|MED|LOW)\]\s*(.+)$", re.IGNORECASE)


def parse_tasks(markdown: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse AGENT_TASKS.md into (active_tasks, stretch_goals).

    Anything under a "## Stretch Goals" (case-insensitive) level-2 heading
    is treated as fallback work.
    """
    lines = markdown.splitlines()
    active: list[dict[str, Any]] = []
    stretch: list[dict[str, Any]] = []
    in_stretch = False
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        (stretch if in_stretch else active).append(current)
        current = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.lower().startswith("## stretch goals"):
            flush()
            in_stretch = True
            continue
        if stripped.startswith("## ") and not stripped.lower().startswith("## stretch"):
            match = TASK_HEADING_RE.match(stripped)
            if match:
                flush()
                completed, priority, title = match.groups()
                current = {
                    "title": title.strip(),
                    "priority": priority.upper(),
                    "completed": bool(completed),
                    "body": "",
                }
            else:
                flush()
            continue
        if current is not None:
            current["body"] += raw + "\n"

    flush()
    return active, stretch


def pick_next_task(
    state: dict[str, Any],
    active: list[dict[str, Any]],
    stretch: list[dict[str, Any]],
) -> dict[str, Any] | None:
    # 1. Slack-injected queue wins.
    if state["queue"]:
        queued = state["queue"].pop(0)
        return {
            "title": queued,
            "priority": "QUEUED",
            "completed": False,
            "body": "(submitted via Slack)",
        }

    # 2. Highest-priority incomplete task from AGENT_TASKS.md.
    incomplete = [t for t in active if not t["completed"]]
    incomplete.sort(key=lambda t: PRIORITY_ORDER.get(t["priority"], 99))
    if incomplete:
        return incomplete[0]

    # 3. Fallback to stretch goals so the agent is never idle.
    stretch_incomplete = [t for t in stretch if not t["completed"]]
    if stretch_incomplete:
        return stretch_incomplete[0]

    return None


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")


def slack_send(text: str) -> None:
    if not SLACK_WEBHOOK_URL:
        log(f"[slack disabled] {text}")
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    except requests.RequestException as exc:
        log(f"slack send failed: {exc}")


def poll_slack_for_tasks(state: dict[str, Any]) -> list[str]:
    if not (SLACK_BOT_TOKEN and SLACK_CHANNEL_ID):
        return []
    try:
        response = requests.get(
            "https://slack.com/api/conversations.history",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={
                "channel": SLACK_CHANNEL_ID,
                "oldest": state.get("last_slack_poll_ts", "0"),
                "limit": 100,
            },
            timeout=10,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        log(f"slack poll failed: {exc}")
        return []

    if not data.get("ok"):
        log(f"slack poll error: {data.get('error')}")
        return []

    new_tasks: list[str] = []
    messages = data.get("messages", [])
    newest_ts = state.get("last_slack_poll_ts", "0")
    for msg in messages:
        ts = msg.get("ts", "0")
        if float(ts) > float(newest_ts):
            newest_ts = ts
        text = (msg.get("text") or "").strip()
        if text.lower().startswith("!task"):
            task_text = text[5:].strip(" :-")
            if task_text:
                new_tasks.append(task_text)

    state["last_slack_poll_ts"] = newest_ts
    return new_tasks


# ---------------------------------------------------------------------------
# Tool implementations (client side)
# ---------------------------------------------------------------------------


class BlockerRaised(Exception):
    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


class TaskComplete(Exception):
    def __init__(self, summary: str) -> None:
        super().__init__(summary)
        self.summary = summary


def _safe_repo_path(path: str) -> Path:
    """Resolve `path` against the work dir and reject escapes or forbidden files."""
    work_dir = RunContext.work_dir
    candidate = (work_dir / path).resolve()
    try:
        rel = candidate.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise PermissionError(f"path escapes repo root: {path}") from exc
    rel_str = str(rel)
    if rel_str in FORBIDDEN_PATHS or any(rel_str.startswith(p) for p in FORBIDDEN_PREFIXES):
        raise PermissionError(f"path is off-limits: {rel_str}")
    return candidate


def tool_read_file(args: dict[str, Any]) -> str:
    path = args["path"]
    target = _safe_repo_path(path)
    if not target.exists():
        return f"ERROR: file does not exist: {path}"
    if not target.is_file():
        return f"ERROR: not a regular file: {path}"
    try:
        text = target.read_text()
    except UnicodeDecodeError:
        return f"ERROR: {path} is not a text file"
    if len(text) > 60_000:
        text = text[:60_000] + "\n\n... [truncated]"
    return text


def tool_list_files(args: dict[str, Any]) -> str:
    pattern = args.get("pattern", "**/*")
    work_dir = RunContext.work_dir
    matches = sorted(str(p.relative_to(work_dir)) for p in work_dir.glob(pattern) if p.is_file())
    matches = [m for m in matches if not any(m.startswith(pref) for pref in FORBIDDEN_PREFIXES)]
    return "\n".join(matches[:200]) or "(no matches)"


def tool_write_file(args: dict[str, Any], dry_run: bool) -> str:
    path = args["path"]
    content = args["content"]
    target = _safe_repo_path(path)
    if dry_run:
        log(f"[dry-run] would write {path} ({len(content)} bytes)")
        return f"DRY-RUN: would write {len(content)} bytes to {path}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    log(f"wrote {path} ({len(content)} bytes)")
    return f"wrote {len(content)} bytes to {path}"


# Commands the agent is not allowed to run. Blocks deploys, destructive git,
# and long-running servers.
FORBIDDEN_COMMAND_PATTERNS = [
    r"\brender\s+deploy\b",
    r"\bgit\s+push\b.*\bmain\b",
    r"\bgit\s+push\s+--force\b",
    r"\bgit\s+push\s+-f\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\brm\s+-rf\s+/",
    r"\bnpm\s+run\s+dev\b",
    r"\buvicorn\b.*--reload\b",
    r"\bcurl\b.*supabase\.co.*--data",
]


def _command_is_forbidden(cmd: str) -> str | None:
    for pattern in FORBIDDEN_COMMAND_PATTERNS:
        if re.search(pattern, cmd, flags=re.IGNORECASE):
            return pattern
    return None


def tool_run_shell(args: dict[str, Any], dry_run: bool) -> str:
    cmd = args["command"]
    forbidden = _command_is_forbidden(cmd)
    if forbidden:
        log(f"refusing command (matched {forbidden}): {cmd}")
        return f"ERROR: command refused (matched forbidden pattern {forbidden!r})"
    if dry_run:
        log(f"[dry-run] would run: {cmd}")
        return f"DRY-RUN: would execute: {cmd}"
    log(f"running shell: {cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=RunContext.work_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 300s"
    output = (result.stdout or "") + (result.stderr or "")
    if len(output) > 15_000:
        output = output[:15_000] + "\n... [truncated]"
    return f"exit={result.returncode}\n{output}"


def tool_ask_user(args: dict[str, Any]) -> str:
    raise BlockerRaised(args["question"])


def tool_mark_complete(args: dict[str, Any]) -> str:
    raise TaskComplete(args["summary"])


TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read a file in the Yardsale work dir. Paths are relative to the repo root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in the repo matching a glob pattern (e.g. 'frontend/src/**/*.jsx').",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file in the repo. Never for secrets or config.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_shell",
        "description": "Run a shell command in the repo root. Use for builds, tests, git add/commit, or linting. Do NOT use for deploys, destructive git operations, pushing to main, or long-running servers.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "ask_user",
        "description": "Stop working and ask Jasha a question via Slack. Use when a decision could break production (schema changes, deployments, architectural direction).",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "mark_complete",
        "description": "Mark the task complete. Provide a short summary of what was changed and why.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]


def execute_tool(name: str, args: dict[str, Any], dry_run: bool) -> str:
    try:
        if name == "read_file":
            return tool_read_file(args)
        if name == "list_files":
            return tool_list_files(args)
        if name == "write_file":
            return tool_write_file(args, dry_run)
        if name == "run_shell":
            return tool_run_shell(args, dry_run)
        if name == "ask_user":
            return tool_ask_user(args)
        if name == "mark_complete":
            return tool_mark_complete(args)
        return f"ERROR: unknown tool {name}"
    except (BlockerRaised, TaskComplete):
        raise
    except PermissionError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Claude loop
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are the autonomous coding agent for Yardsale, a peer-to-peer
local marketplace (React + Vite frontend, FastAPI + Supabase backend). You are
editing a live repository on behalf of Jasha, the owner, while he is at his day
job. He will review your commits on a branch before merging anything.

You are working on a dedicated agent branch (agent/YYYY-MM-DD). Your commits go
there — NEVER push to main, NEVER deploy to production.

Ground rules (non-negotiable):
1. NEVER modify database schema, migrations, or Supabase config without using
   `ask_user` first. Schema lives in the Supabase dashboard.
2. NEVER push to main. Commit on the current branch only. The branch is
   pushed back to GitHub automatically at the end of each task.
3. Commit in small, logical chunks. Use conventional commit messages
   (feat:, fix:, refactor:, docs:, test:, chore:).
4. If a design decision is ambiguous or could break existing behavior, stop
   and call `ask_user`. Jasha prefers a question over a broken feature.
5. Never guess. If you need context about a file, read it first. If you can't
   find it, `ask_user` instead of inventing behavior.
6. When done, call `mark_complete` with a one-paragraph summary.

Workflow for each task:
- Read the task description carefully. Note any file paths it mentions.
- Read those files and any obvious adjacent files (imports, related routes).
- Make the smallest change that implements the task correctly.
- Run lint/tests only if they exist for the touched area. Don't invent a test
  suite that isn't there.
- Stage specific files: `git add frontend/src/foo.jsx` — never `git add -A`.
- Commit with a clear message. Do not push.
- Call `mark_complete` with a summary for Jasha's review.

You have tools: read_file, list_files, write_file, run_shell, ask_user,
mark_complete. Prefer reading before writing. Prefer asking before guessing."""


def estimate_cost(usage: Any) -> float:
    """Rough USD cost for a Sonnet 4.6 message."""
    input_tok = getattr(usage, "input_tokens", 0) or 0
    output_tok = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        input_tok * 3e-6
        + output_tok * 15e-6
        + cache_read * 0.3e-6
        + cache_write * 3.75e-6
    )


def extract_context_paths(body: str) -> list[str]:
    candidates = re.findall(r"`([^`]+)`", body) + re.findall(
        r"(\S+\.(?:py|jsx|js|ts|tsx|md|json|css))", body
    )
    seen: set[str] = set()
    paths: list[str] = []
    work_dir = RunContext.work_dir
    for c in candidates:
        c = c.strip(".,;:()[]")
        candidate_path = work_dir / c
        if c and c not in seen and candidate_path.exists() and candidate_path.is_file():
            seen.add(c)
            paths.append(c)
    return paths[:6]


def build_initial_message(task: dict[str, Any]) -> list[dict[str, Any]]:
    work_dir = RunContext.work_dir
    context_blocks: list[str] = []
    for path in extract_context_paths(task.get("body", "")):
        try:
            contents = (work_dir / path).read_text()
            if len(contents) > 20_000:
                contents = contents[:20_000] + "\n... [truncated]"
            context_blocks.append(f"=== {path} ===\n{contents}")
        except (OSError, UnicodeDecodeError):
            continue

    preloaded = (
        "\n\n".join(context_blocks)
        if context_blocks
        else "(no files preloaded — use read_file as needed)"
    )

    user_text = (
        f"# Task ({task['priority']}): {task['title']}\n\n"
        f"## Description\n{task.get('body', '').strip() or '(no details provided)'}\n\n"
        f"## Preloaded source files\n{preloaded}\n\n"
        "Begin. Read any additional files you need, then make the changes. "
        "Call mark_complete when done, or ask_user if you hit a blocker."
    )
    return [{"role": "user", "content": user_text}]


def run_task(
    client: anthropic.Anthropic,
    task: dict[str, Any],
    state: dict[str, Any],
    dry_run: bool,
    cost_cap: float,
) -> str:
    log(f"task start: [{task['priority']}] {task['title']}")
    state["current_task"] = task["title"]
    state["status"] = "working"
    state["blocked_reason"] = None
    state["blocked_question"] = None
    save_state(state)

    messages = build_initial_message(task)

    for _ in range(MAX_TOOL_ITERATIONS):
        if state.get("cost_today_usd", 0.0) >= cost_cap:
            log(f"daily cost cap reached (${cost_cap}) — stopping task")
            slack_send(
                f":moneybag: Agent hit daily cost cap (${cost_cap}) on *{task['title']}*. Stopping."
            )
            state["status"] = "idle"
            state["blocked_reason"] = "cost_cap"
            save_state(state)
            return "cost_cap"

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 — any failure must stop the run
            log(f"claude API call failed: {type(exc).__name__}: {exc}")
            state["status"] = "error"
            state["blocked_reason"] = "api_error"
            save_state(state)
            slack_send(
                f":rotating_light: Yardsale agent stopped on *{task['title']}* — "
                f"Claude API call failed ({type(exc).__name__}): {exc}"
            )
            return "api_error"

        state["cost_today_usd"] = round(
            state.get("cost_today_usd", 0.0) + estimate_cost(response.usage), 4
        )
        save_state(state)

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in response.content if b.type == "text")
            log(f"task finished without mark_complete: {text[:200]}")
            return "finished_no_mark"

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tool_use in tool_uses:
            log(f"tool call: {tool_use.name}({json.dumps(tool_use.input)[:200]})")
            try:
                result = execute_tool(tool_use.name, tool_use.input, dry_run)
            except BlockerRaised as blocker:
                state["status"] = "blocked"
                state["blocked_reason"] = "asked_user"
                state["blocked_question"] = blocker.question
                save_state(state)
                slack_send(
                    f":warning: Yardsale agent is blocked on *{task['title']}*\n>{blocker.question}"
                )
                log(f"blocked: {blocker.question}")
                return "blocked"
            except TaskComplete as done:
                state["status"] = "idle"
                state["current_task"] = None
                state["completed_today"].append(
                    {
                        "title": task["title"],
                        "summary": done.summary,
                        "at": now_iso(),
                    }
                )
                save_state(state)
                slack_send(f":white_check_mark: Finished *{task['title']}*\n>{done.summary}")
                log(f"complete: {done.summary}")
                return "complete"

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    log("hit max tool iterations without completing")
    slack_send(f":warning: Agent hit iteration limit on *{task['title']}*. Needs a look.")
    return "iteration_limit"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_once(dry_run: bool, cost_cap: float) -> str | None:
    state = load_state()

    if not TASKS_FILE.exists():
        log(f"no AGENT_TASKS.md at {TASKS_FILE} — nothing to do")
        return None

    new_tasks = poll_slack_for_tasks(state)
    if new_tasks:
        state["queue"].extend(new_tasks)
        for t in new_tasks:
            log(f"queued from slack: {t}")
        save_state(state)

    active, stretch = parse_tasks(TASKS_FILE.read_text())
    task = pick_next_task(state, active, stretch)
    if task is None:
        state["status"] = "idle"
        save_state(state)
        log("no tasks available")
        return None

    client = anthropic.Anthropic()
    status = run_task(client, task, state, dry_run, cost_cap)

    # Push the agent branch after each successful completion so Jasha can review.
    if status == "complete" and not dry_run:
        branch = f"{AGENT_BRANCH_PREFIX}{today_key()}"
        push_agent_branch(RunContext.work_dir, branch)

    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Yardsale autonomous agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without writing files or running commands.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single task and exit (default runs until time budget).",
    )
    parser.add_argument(
        "--hours", type=float, default=DEFAULT_RUN_HOURS, help="Max wall-clock runtime in hours."
    )
    parser.add_argument(
        "--cost-cap",
        type=float,
        default=float(os.getenv("DAILY_COST_CAP_USD", DEFAULT_DAILY_COST_CAP_USD)),
        help="Hard daily USD cap on Claude spend.",
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Skip cloning Yardsale (use an existing work dir — mainly for local testing).",
    )
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY not set")
        return 1

    log(
        f"agent starting (dry_run={args.dry_run}, hours={args.hours}, once={args.once}, "
        f"cost_cap=${args.cost_cap})"
    )
    slack_send(f":robot_face: Yardsale agent starting (dry_run={args.dry_run})")

    if args.skip_clone:
        RunContext.work_dir = DEFAULT_WORK_DIR
    else:
        try:
            prepare_work_dir()
            ensure_agent_branch(RunContext.work_dir)
        except subprocess.CalledProcessError as exc:
            log(f"git setup failed: {exc.stderr or exc}")
            slack_send(":x: Agent failed during git clone/checkout. Check logs.")
            return 1

    if args.once:
        status = run_once(args.dry_run, args.cost_cap)
        return 1 if status == "api_error" else 0

    deadline = time.monotonic() + args.hours * 3600
    while time.monotonic() < deadline:
        status = run_once(args.dry_run, args.cost_cap)
        if status == "api_error":
            log("claude API error — stopping run (Slack notified from run_task)")
            return 1
        if status == "blocked":
            log(f"pausing {SLACK_POLL_INTERVAL_SEC}s waiting for user input")
            time.sleep(SLACK_POLL_INTERVAL_SEC)
            continue
        if status == "cost_cap":
            log("cost cap hit — sleeping until end of day")
            time.sleep(SLACK_POLL_INTERVAL_SEC)
            continue
        if status is None:
            log(f"idle — polling again in {SLACK_POLL_INTERVAL_SEC}s")
            time.sleep(SLACK_POLL_INTERVAL_SEC)
            continue

    log("time budget exhausted — shutting down")
    slack_send(":moon: Yardsale agent finished for the day.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
