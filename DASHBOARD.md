# Dashboard

`dashboard/yardsale-agent-dashboard.jsx` is a read-only React component that displays the agent's current task, queue, completed work, activity log, and daily spend. It polls a state URL every 30 seconds.

## How state gets from the agent to the dashboard

The agent runs on Render (ephemeral filesystem, no public URL). The dashboard runs on Netlify/Vercel/localhost. They need a shared storage layer. Three options are wired into `agent.py` — pick one.

### Option A — Secret GitHub Gist (recommended)

Zero infrastructure. Free. Works from anywhere.

1. Go to https://gist.github.com → **+** → New gist → create a **secret** gist.
2. Name the file `agent_state.json`. Put `{}` in the body. **Create secret gist.**
3. Copy the gist ID from the URL (`https://gist.github.com/jashadev42/<GIST_ID>`).
4. In `.env` (or Render env vars), set:
   ```
   DASHBOARD_GIST_ID=<that id>
   GITHUB_TOKEN=<PAT with `gist` scope>
   ```
5. In the dashboard, set the state URL to the gist raw URL:
   ```
   VITE_AGENT_STATE_URL=https://gist.githubusercontent.com/jashadev42/<GIST_ID>/raw/agent_state.json
   ```

The agent will PATCH the gist on every state change. The dashboard fetches the raw URL every 30 seconds.

> Secret gists are not indexed by search engines but are reachable by anyone who knows the URL. Don't store secrets in the agent state (the current schema doesn't).

### Option B — Shared local file

Only useful if the agent and dashboard run on the same machine (local dev).

```
DASHBOARD_STATE_PATH=/path/to/dashboard/public/agent_state.json
```

The dashboard's dev server serves `public/` at `/agent_state.json`, which is the default URL the dashboard reads.

### Option C — HTTP webhook

If you have your own backend (tiny Flask/FastAPI/Cloudflare Worker that stores the JSON):

```
DASHBOARD_WEBHOOK_URL=https://your-api.example.com/agent-state
```

The agent POSTs the full state JSON to that URL on every update. Your backend is responsible for persisting it and exposing a GET endpoint that the dashboard reads.

## State schema

The agent writes this shape on every update:

```json
{
  "current_task": "Implement hashtag filtering",
  "status": "working",               // "idle" | "working" | "blocked"
  "queue": ["add loading skeleton"], // Slack !task injections
  "completed_today": [
    {
      "title": "Fix mobile nav overflow",
      "summary": "Added overflow-x-auto to nav container in Layout.jsx",
      "at": "2026-04-10T14:32:01+00:00"
    }
  ],
  "blocked_reason": null,              // "asked_user" | "cost_cap" | null
  "blocked_question": null,            // text of the question when blocked
  "last_slack_poll_ts": "1712756400.123456",
  "last_updated": "2026-04-10T14:35:12+00:00",
  "cost_day": "2026-04-10",
  "cost_today_usd": 1.27
}
```

The dashboard component consumes this shape directly — no transformation needed.

## Wiring the dashboard into a React app

The component is self-contained (only imports `react`). To render it anywhere:

```jsx
import YardsaleAgentDashboard from "./yardsale-agent-dashboard.jsx";

function App() {
  return (
    <YardsaleAgentDashboard
      stateUrl="https://gist.githubusercontent.com/jashadev42/<GIST_ID>/raw/agent_state.json"
    />
  );
}
```

Or set `VITE_AGENT_STATE_URL` in a `.env` file and drop `<YardsaleAgentDashboard />` into a route.

## Reply-from-dashboard

Not supported in the current dashboard — it's read-only. Reply to the agent in the `#yardsale-agent` Slack channel. The agent polls Slack every 3 minutes and will pick up the message on the next cycle.

## Log visibility

If you want the Log tab to show content, the dashboard will also try to fetch `agent_log.txt` from the same directory as the state URL. For the Gist option, add a second file named `agent_log.txt` to the same gist and the agent will need a small extension to also PATCH it (not wired by default — let me know if you want this).
