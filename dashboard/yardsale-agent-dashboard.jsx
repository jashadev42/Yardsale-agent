// Yardsale Agent Dashboard
// ---------------------------------------------------------------
// Read-only status view for the autonomous Yardsale agent. Fetches
// agent_state.json from a configurable URL every 30 seconds and
// renders tasks, the activity log, completed work, and today's spend.
//
// Set the state URL via:
//   - Vite: VITE_AGENT_STATE_URL
//   - CRA:  REACT_APP_AGENT_STATE_URL
//   - or pass `stateUrl` as a prop
//
// The agent publishes state via DASHBOARD_STATE_PATH (file) or
// DASHBOARD_WEBHOOK_URL (HTTP POST) — see the agent's .env.example.
// A common hosted pattern: the agent PATCHes a private GitHub Gist
// with the state JSON, and this dashboard fetches the gist raw URL.

import React, { useEffect, useState, useCallback } from "react";

const DEFAULT_REFRESH_MS = 30_000;

const resolveStateUrl = (override) => {
  if (override) return override;
  const viteEnv = typeof import.meta !== "undefined" && import.meta.env;
  if (viteEnv?.VITE_AGENT_STATE_URL) return viteEnv.VITE_AGENT_STATE_URL;
  if (typeof process !== "undefined" && process.env?.REACT_APP_AGENT_STATE_URL) {
    return process.env.REACT_APP_AGENT_STATE_URL;
  }
  return "/agent_state.json";
};

const statusStyles = {
  idle: { bg: "#f3f4f6", text: "#6b7280", label: "Idle" },
  working: { bg: "#dbeafe", text: "#1d4ed8", label: "Working" },
  blocked: { bg: "#fef3c7", text: "#b45309", label: "Blocked" },
};

const priorityStyles = {
  HIGH: { bg: "#fee2e2", text: "#b91c1c" },
  MED: { bg: "#fef3c7", text: "#92400e" },
  LOW: { bg: "#e5e7eb", text: "#374151" },
  QUEUED: { bg: "#dbeafe", text: "#1d4ed8" },
};

function Badge({ style, children }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 600,
        background: style?.bg || "#e5e7eb",
        color: style?.text || "#374151",
      }}
    >
      {children}
    </span>
  );
}

function TabButton({ active, onClick, children, badge }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "10px 16px",
        background: "none",
        border: "none",
        borderBottom: active ? "2px solid #2563eb" : "2px solid transparent",
        color: active ? "#2563eb" : "#6b7280",
        fontWeight: active ? 600 : 500,
        cursor: "pointer",
        position: "relative",
      }}
    >
      {children}
      {badge > 0 && (
        <span
          style={{
            marginLeft: 6,
            display: "inline-block",
            minWidth: 18,
            height: 18,
            padding: "0 5px",
            borderRadius: 9,
            background: "#ef4444",
            color: "white",
            fontSize: 11,
            fontWeight: 700,
            lineHeight: "18px",
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

function Card({ children, style }) {
  return (
    <div
      style={{
        background: "white",
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        padding: 16,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function CurrentTaskBanner({ state }) {
  const style = statusStyles[state.status] || statusStyles.idle;
  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 4 }}>CURRENT TASK</div>
          <div style={{ fontSize: 18, fontWeight: 600 }}>
            {state.current_task || "(none — agent is idle)"}
          </div>
          {state.blocked_question && (
            <div
              style={{
                marginTop: 8,
                padding: 8,
                background: "#fef3c7",
                borderRadius: 6,
                fontSize: 13,
                color: "#92400e",
              }}
            >
              <strong>Blocked:</strong> {state.blocked_question}
              <div style={{ marginTop: 6, fontSize: 11, opacity: 0.8 }}>
                Reply in Slack to unblock.
              </div>
            </div>
          )}
        </div>
        <Badge style={style}>{style.label}</Badge>
      </div>
    </Card>
  );
}

function TasksTab({ state }) {
  const queue = state.queue || [];
  const completed = state.completed_today || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card>
        <h3 style={{ margin: "0 0 12px", fontSize: 14, color: "#374151" }}>Queue ({queue.length})</h3>
        {queue.length === 0 ? (
          <div style={{ color: "#9ca3af", fontSize: 13 }}>
            No queued tasks. Send <code>!task ...</code> in Slack to add one.
          </div>
        ) : (
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: 14 }}>
            {queue.map((t, i) => (
              <li key={i} style={{ padding: "4px 0" }}>
                {t}
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card>
        <h3 style={{ margin: "0 0 12px", fontSize: 14, color: "#374151" }}>
          Completed today ({completed.length})
        </h3>
        {completed.length === 0 ? (
          <div style={{ color: "#9ca3af", fontSize: 13 }}>Nothing yet today.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {completed.map((task, i) => (
              <div
                key={i}
                style={{
                  padding: 10,
                  background: "#f9fafb",
                  borderRadius: 6,
                  borderLeft: "3px solid #10b981",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 14 }}>{task.title}</div>
                <div style={{ fontSize: 12, color: "#6b7280", marginTop: 4 }}>{task.summary}</div>
                <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 4 }}>{task.at}</div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function LogTab({ logText }) {
  return (
    <Card style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
      {logText ? (
        <pre style={{ margin: 0, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{logText}</pre>
      ) : (
        <div style={{ color: "#9ca3af" }}>
          Log not available. Set an <code>AGENT_LOG_URL</code> or expose{" "}
          <code>agent_log.txt</code> next to the state JSON.
        </div>
      )}
    </Card>
  );
}

function MessagesTab({ state }) {
  const messages = [];
  if (state.current_task) {
    messages.push({
      from: "agent",
      text: `Working on: ${state.current_task}`,
      at: state.last_updated,
    });
  }
  if (state.blocked_question) {
    messages.push({
      from: "agent",
      text: `❓ ${state.blocked_question}`,
      at: state.last_updated,
    });
  }
  (state.completed_today || []).forEach((c) => {
    messages.push({ from: "agent", text: `✅ ${c.title}: ${c.summary}`, at: c.at });
  });

  return (
    <Card>
      {messages.length === 0 ? (
        <div style={{ color: "#9ca3af", fontSize: 13 }}>No messages yet today.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {messages.map((m, i) => (
            <div
              key={i}
              style={{
                padding: 10,
                background: "#f9fafb",
                borderRadius: 6,
                fontSize: 13,
              }}
            >
              <div style={{ fontSize: 11, color: "#9ca3af", marginBottom: 4 }}>
                {m.from} · {m.at}
              </div>
              {m.text}
            </div>
          ))}
        </div>
      )}
      <div
        style={{
          marginTop: 12,
          padding: 10,
          background: "#eff6ff",
          borderRadius: 6,
          fontSize: 12,
          color: "#1e40af",
        }}
      >
        Reply to the agent in your Slack <code>#yardsale-agent</code> channel. The dashboard is
        read-only.
      </div>
    </Card>
  );
}

function CostFooter({ state }) {
  const today = state.cost_today_usd || 0;
  return (
    <div
      style={{
        position: "sticky",
        bottom: 0,
        marginTop: 16,
        padding: 12,
        background: "white",
        borderTop: "1px solid #e5e7eb",
        display: "flex",
        justifyContent: "space-around",
        fontSize: 13,
      }}
    >
      <div>
        <div style={{ color: "#6b7280", fontSize: 11 }}>TODAY</div>
        <div style={{ fontWeight: 600 }}>${today.toFixed(2)}</div>
      </div>
      <div>
        <div style={{ color: "#6b7280", fontSize: 11 }}>WEEK (est)</div>
        <div style={{ fontWeight: 600 }}>${(today * 5).toFixed(2)}</div>
      </div>
      <div>
        <div style={{ color: "#6b7280", fontSize: 11 }}>MONTH (est)</div>
        <div style={{ fontWeight: 600 }}>${(today * 22).toFixed(2)}</div>
      </div>
    </div>
  );
}

export default function YardsaleAgentDashboard({ stateUrl: stateUrlProp, refreshMs = DEFAULT_REFRESH_MS }) {
  const stateUrl = resolveStateUrl(stateUrlProp);
  const [state, setState] = useState(null);
  const [logText, setLogText] = useState("");
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("tasks");

  const fetchState = useCallback(async () => {
    try {
      const res = await fetch(`${stateUrl}?t=${Date.now()}`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      setState(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, [stateUrl]);

  const fetchLog = useCallback(async () => {
    const logUrl = stateUrl.replace(/agent_state\.json$/, "agent_log.txt");
    if (logUrl === stateUrl) return;
    try {
      const res = await fetch(`${logUrl}?t=${Date.now()}`);
      if (res.ok) {
        const text = await res.text();
        setLogText(text.split("\n").slice(-100).join("\n"));
      }
    } catch {
      // log is optional
    }
  }, [stateUrl]);

  useEffect(() => {
    fetchState();
    fetchLog();
    const id = setInterval(() => {
      fetchState();
      fetchLog();
    }, refreshMs);
    return () => clearInterval(id);
  }, [fetchState, fetchLog, refreshMs]);

  if (error && !state) {
    return (
      <div style={{ padding: 24, fontFamily: "system-ui, sans-serif" }}>
        <h2>Yardsale Agent Dashboard</h2>
        <Card style={{ borderColor: "#fca5a5", background: "#fef2f2" }}>
          <div style={{ color: "#991b1b", fontWeight: 600 }}>Could not load state</div>
          <div style={{ color: "#7f1d1d", fontSize: 13, marginTop: 6 }}>{error}</div>
          <div style={{ color: "#6b7280", fontSize: 12, marginTop: 8 }}>
            Tried: <code>{stateUrl}</code>
          </div>
        </Card>
      </div>
    );
  }

  if (!state) {
    return <div style={{ padding: 24, fontFamily: "system-ui, sans-serif" }}>Loading…</div>;
  }

  const blockerBadge = state.status === "blocked" ? 1 : 0;

  return (
    <div
      style={{
        maxWidth: 720,
        margin: "0 auto",
        padding: 16,
        fontFamily: "system-ui, sans-serif",
        background: "#f9fafb",
        minHeight: "100vh",
      }}
    >
      <header style={{ marginBottom: 16 }}>
        <h1 style={{ margin: 0, fontSize: 20 }}>Yardsale Agent</h1>
        <div style={{ fontSize: 12, color: "#6b7280" }}>
          Last update: {state.last_updated || "—"}
        </div>
      </header>

      <div style={{ marginBottom: 16 }}>
        <CurrentTaskBanner state={state} />
      </div>

      <div style={{ display: "flex", borderBottom: "1px solid #e5e7eb", marginBottom: 16 }}>
        <TabButton active={tab === "tasks"} onClick={() => setTab("tasks")}>
          Tasks
        </TabButton>
        <TabButton
          active={tab === "messages"}
          onClick={() => setTab("messages")}
          badge={blockerBadge}
        >
          Messages
        </TabButton>
        <TabButton active={tab === "log"} onClick={() => setTab("log")}>
          Log
        </TabButton>
      </div>

      {tab === "tasks" && <TasksTab state={state} />}
      {tab === "messages" && <MessagesTab state={state} />}
      {tab === "log" && <LogTab logText={logText} />}

      <CostFooter state={state} />
    </div>
  );
}
