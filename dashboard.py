"""
dashboard.py - Local web dashboard served on localhost:8080.
"""

import json
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs

DB_PATH = Path.home() / ".claude" / "usage.db"
PROJECTS_DIR = Path.home() / ".claude" / "projects"


def get_dashboard_data(db_path=DB_PATH):
    if not db_path.exists():
        return {"error": "Database not found. Run: python cli.py scan"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── All models (for filter UI) ────────────────────────────────────────────
    model_rows = conn.execute("""
        SELECT COALESCE(model, 'unknown') as model
        FROM turns
        GROUP BY model
        ORDER BY SUM(input_tokens + output_tokens) DESC
    """).fetchall()
    all_models = [r["model"] for r in model_rows]

    # ── Daily per-model, ALL history (client filters by range) ────────────────
    daily_rows = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)   as day,
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as input,
            SUM(output_tokens)         as output,
            SUM(cache_read_tokens)     as cache_read,
            SUM(cache_creation_tokens) as cache_creation,
            COUNT(*)                   as turns
        FROM turns
        GROUP BY day, model
        ORDER BY day, model
    """).fetchall()

    daily_by_model = [{
        "day":            r["day"],
        "model":          r["model"],
        "input":          r["input"] or 0,
        "output":         r["output"] or 0,
        "cache_read":     r["cache_read"] or 0,
        "cache_creation": r["cache_creation"] or 0,
        "turns":          r["turns"] or 0,
    } for r in daily_rows]

    # ── All sessions (client filters by range and model) ──────────────────────
    session_rows = conn.execute("""
        SELECT
            session_id, project_name, first_timestamp, last_timestamp,
            total_input_tokens, total_output_tokens,
            total_cache_read, total_cache_creation, model, turn_count
        FROM sessions
        ORDER BY last_timestamp DESC
    """).fetchall()

    sessions_all = []
    for r in session_rows:
        try:
            t1 = datetime.fromisoformat(r["first_timestamp"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(r["last_timestamp"].replace("Z", "+00:00"))
            duration_min = round((t2 - t1).total_seconds() / 60, 1)
        except Exception:
            duration_min = 0
        sessions_all.append({
            "session_id":    r["session_id"],
            "session_short": r["session_id"][:8],
            "project":       r["project_name"] or "unknown",
            "last":          (r["last_timestamp"] or "")[:16].replace("T", " "),
            "last_date":     (r["last_timestamp"] or "")[:10],
            "duration_min":  duration_min,
            "model":         r["model"] or "unknown",
            "turns":         r["turn_count"] or 0,
            "input":         r["total_input_tokens"] or 0,
            "output":        r["total_output_tokens"] or 0,
            "cache_read":    r["total_cache_read"] or 0,
            "cache_creation": r["total_cache_creation"] or 0,
        })

    conn.close()

    return {
        "all_models":     all_models,
        "daily_by_model": daily_by_model,
        "sessions_all":   sessions_all,
        "generated_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def extract_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            text = extract_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        item_type = value.get("type")
        if item_type in {"text", "input_text", "output_text"}:
            return str(value.get("text", "")).strip()
        if item_type in {"tool_use", "server_tool_use"}:
            name = value.get("name") or "tool"
            payload = value.get("input")
            payload_text = ""
            if payload not in (None, "", {}, []):
                try:
                    payload_text = json.dumps(payload, ensure_ascii=False)
                except TypeError:
                    payload_text = str(payload)
            return f"[tool_use] {name}" + (f" {payload_text}" if payload_text else "")
        if item_type in {"tool_result", "server_tool_result"}:
            content = extract_text(value.get("content"))
            return f"[tool_result] {content}".strip()
        for key in ("text", "content", "message"):
            if key in value:
                text = extract_text(value.get(key))
                if text:
                    return text
        return ""
    return str(value).strip()


def find_session_file(session_id, projects_dir=PROJECTS_DIR):
    for path in projects_dir.glob("**/*.jsonl"):
        if path.stem == session_id:
            return path

    candidate = None
    for path in projects_dir.glob("**/*.jsonl"):
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if session_id in line:
                        candidate = path
                        break
        except OSError:
            continue
        if candidate:
            break
    return candidate


def get_session_detail(session_id, projects_dir=PROJECTS_DIR):
    path = find_session_file(session_id, projects_dir=projects_dir)
    if not path:
        return {"error": f"Session not found for id {session_id}"}

    messages = []
    session_meta = {
        "session_id": session_id,
        "session_short": session_id[:8],
        "project": "unknown",
        "git_branch": "",
        "source_file": str(path),
        "first_timestamp": "",
        "last_timestamp": "",
    }

    first_user_message = ""
    assistant_count = 0

    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if record.get("sessionId") != session_id:
                    continue

                timestamp = record.get("timestamp", "")
                cwd = record.get("cwd", "")
                git_branch = record.get("gitBranch", "")
                if cwd:
                    parts = cwd.replace("\\", "/").rstrip("/").split("/")
                    session_meta["project"] = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "unknown")
                if git_branch and not session_meta["git_branch"]:
                    session_meta["git_branch"] = git_branch
                if timestamp:
                    if not session_meta["first_timestamp"] or timestamp < session_meta["first_timestamp"]:
                        session_meta["first_timestamp"] = timestamp
                    if not session_meta["last_timestamp"] or timestamp > session_meta["last_timestamp"]:
                        session_meta["last_timestamp"] = timestamp

                rtype = record.get("type")
                if rtype not in {"user", "assistant"}:
                    continue

                content = extract_text(
                    record.get("message", {}).get("content") if rtype == "assistant" else record.get("message")
                )
                if not content:
                    continue

                usage = record.get("message", {}).get("usage", {}) if rtype == "assistant" else {}
                if rtype == "user" and not first_user_message:
                    first_user_message = content.replace("\n", " ").strip()
                if rtype == "assistant":
                    assistant_count += 1
                messages.append({
                    "role": rtype,
                    "timestamp": timestamp,
                    "model": record.get("message", {}).get("model", "") if rtype == "assistant" else "",
                    "input_tokens": usage.get("input_tokens", 0) or 0,
                    "output_tokens": usage.get("output_tokens", 0) or 0,
                    "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
                    "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
                    "content": content,
                })
    except OSError as e:
        return {"error": f"Could not read session file: {e}"}

    if not messages:
        return {"error": f"No readable transcript content found for session {session_id}"}

    return {
        "session": session_meta,
        "summary": {
            "message_count": len(messages),
            "assistant_count": assistant_count,
            "first_user_excerpt": (first_user_message[:180] + "…") if len(first_user_message) > 180 else first_user_message,
        },
        "messages": messages,
    }


def get_session_preview(session_id, projects_dir=PROJECTS_DIR):
    detail = get_session_detail(session_id, projects_dir=projects_dir)
    if detail.get("error"):
        return detail

    meta = detail["session"]
    summary = detail.get("summary", {})
    messages = detail.get("messages", [])

    assistant_messages = [msg for msg in messages if msg.get("role") == "assistant"]
    primary_model = next((msg.get("model") for msg in assistant_messages if msg.get("model")), "unknown")
    total_input = sum(msg.get("input_tokens", 0) or 0 for msg in assistant_messages)
    total_output = sum(msg.get("output_tokens", 0) or 0 for msg in assistant_messages)

    return {
        "session_id": session_id,
        "session_short": meta.get("session_short", session_id[:8]),
        "project": meta.get("project", "unknown"),
        "first_timestamp": meta.get("first_timestamp", ""),
        "last_timestamp": meta.get("last_timestamp", ""),
        "model": primary_model,
        "message_count": summary.get("message_count", len(messages)),
        "assistant_count": summary.get("assistant_count", len(assistant_messages)),
        "first_user_excerpt": summary.get("first_user_excerpt", ""),
        "token_total": total_input + total_output,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Usage Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e2e8f0;
    --muted: #8892a4;
    --accent: #d97757;
    --blue: #4f8ef7;
    --green: #4ade80;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }

  header { background: var(--card); border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 18px; font-weight: 600; color: var(--accent); }
  header .meta { color: var(--muted); font-size: 12px; }

  #filter-bar { background: var(--card); border-bottom: 1px solid var(--border); padding: 10px 24px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .filter-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); white-space: nowrap; }
  .filter-sep { width: 1px; height: 22px; background: var(--border); flex-shrink: 0; }
  #model-checkboxes { display: flex; flex-wrap: wrap; gap: 6px; }
  .model-cb-label { display: flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 20px; border: 1px solid var(--border); cursor: pointer; font-size: 12px; color: var(--muted); transition: border-color 0.15s, color 0.15s, background 0.15s; user-select: none; }
  .model-cb-label:hover { border-color: var(--accent); color: var(--text); }
  .model-cb-label.checked { background: rgba(217,119,87,0.12); border-color: var(--accent); color: var(--text); }
  .model-cb-label input { display: none; }
  .filter-btn { padding: 3px 10px; border-radius: 4px; border: 1px solid var(--border); background: transparent; color: var(--muted); font-size: 11px; cursor: pointer; white-space: nowrap; }
  .filter-btn:hover { border-color: var(--accent); color: var(--text); }
  .range-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; flex-shrink: 0; }
  .range-btn { padding: 4px 13px; background: transparent; border: none; border-right: 1px solid var(--border); color: var(--muted); font-size: 12px; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .range-btn:last-child { border-right: none; }
  .range-btn:hover { background: rgba(255,255,255,0.04); color: var(--text); }
  .range-btn.active { background: rgba(217,119,87,0.15); color: var(--accent); font-weight: 600; }

  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .stat-card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .stat-card .value { font-size: 22px; font-weight: 700; }
  .stat-card .sub { color: var(--muted); font-size: 11px; margin-top: 4px; }

  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }
  .chart-card.wide { grid-column: 1 / -1; }
  .chart-card h2 { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 240px; }
  .chart-wrap.tall { height: 300px; }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); border-bottom: 1px solid var(--border); }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,0.02); }
  .model-tag { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; background: rgba(79,142,247,0.15); color: var(--blue); }
  .cost { color: var(--green); font-family: monospace; }
  .cost-na { color: var(--muted); font-family: monospace; font-size: 11px; }
  .num { font-family: monospace; }
  .muted { color: var(--muted); }
  .section-title { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }
  .table-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 24px; overflow-x: auto; }
  .sessions-table tbody tr { cursor: pointer; }
  .sessions-table tbody tr.active td { background: rgba(217,119,87,0.1); }
  .session-link { display: flex; flex-direction: column; gap: 2px; }
  .session-link-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--text); }
  .session-link-meta { color: var(--muted); font-size: 11px; }
  .session-row-hover { position: relative; }
  .session-hover-card { position: absolute; left: 18px; top: calc(100% + 8px); width: min(420px, 72vw); padding: 14px; border-radius: 14px; background: rgba(17,20,28,0.98); border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 18px 40px rgba(0,0,0,0.38); z-index: 40; pointer-events: none; }
  .session-hover-kicker { color: var(--accent); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .session-hover-title { color: var(--text); font-size: 14px; font-weight: 600; line-height: 1.5; margin-bottom: 10px; }
  .session-hover-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
  .session-hover-pill { display: inline-flex; padding: 4px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); font-size: 10px; background: rgba(255,255,255,0.03); }
  .session-hover-body { color: var(--muted); font-size: 12px; line-height: 1.6; }
  .session-modal-backdrop { position: fixed; inset: 0; background: rgba(8,10,14,0.72); backdrop-filter: blur(6px); display: none; align-items: center; justify-content: center; padding: 24px; z-index: 1000; }
  .session-modal-backdrop.open { display: flex; }
  .session-modal { width: min(1040px, 100%); max-height: min(88vh, 920px); overflow: hidden; background: linear-gradient(180deg, rgba(30,33,43,0.98), rgba(20,23,31,0.98)); border: 1px solid rgba(255,255,255,0.08); border-radius: 18px; box-shadow: 0 30px 90px rgba(0,0,0,0.45); display: flex; flex-direction: column; }
  .session-modal-header { padding: 18px 22px 14px; border-bottom: 1px solid var(--border); display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .session-modal-title { font-size: 18px; font-weight: 700; }
  .session-modal-subtitle { color: var(--muted); margin-top: 4px; }
  .session-modal-controls { display: flex; align-items: center; gap: 10px; }
  .session-detail-close { border: 1px solid var(--border); background: transparent; color: var(--muted); border-radius: 8px; padding: 7px 11px; cursor: pointer; }
  .session-detail-close:hover { border-color: var(--accent); color: var(--text); }
  .session-modal-body { overflow: auto; padding: 20px 22px 24px; }
  .session-hero { margin-bottom: 18px; padding: 18px 20px; border-radius: 16px; background: linear-gradient(135deg, rgba(217,119,87,0.18), rgba(79,142,247,0.08)); border: 1px solid rgba(255,255,255,0.08); }
  .session-kicker { color: var(--accent); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .session-hero-title { font-size: 22px; line-height: 1.3; font-weight: 700; color: var(--text); }
  .session-hero-text { margin-top: 10px; color: var(--muted); line-height: 1.7; max-width: 78ch; }
  .session-overview { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
  .session-overview-card { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 14px; padding: 14px 15px; }
  .session-overview-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .session-overview-value { color: var(--text); font-size: 15px; font-weight: 600; line-height: 1.4; }
  .session-overview-sub { color: var(--muted); font-size: 11px; margin-top: 4px; line-height: 1.5; }
  .session-summary { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; margin-bottom: 18px; }
  .session-summary-card { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 14px; padding: 16px; }
  .session-summary-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
  .session-summary-text { line-height: 1.6; color: var(--text); }
  .session-detail-meta { display: flex; flex-wrap: wrap; gap: 8px; }
  .session-detail-pill { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); font-size: 11px; background: rgba(255,255,255,0.03); }
  .transcript-section-title { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 10px; }
  .session-detail-empty { color: var(--muted); line-height: 1.6; padding: 20px; }
  .session-detail-error { color: #f87171; padding: 20px; }
  .message-list { display: flex; flex-direction: column; gap: 14px; }
  .message-card { border-radius: 16px; padding: 14px 16px; max-width: 88%; }
  .message-card.user { align-self: flex-start; background: rgba(79,142,247,0.12); border: 1px solid rgba(79,142,247,0.28); }
  .message-card.assistant { align-self: flex-end; background: rgba(217,119,87,0.12); border: 1px solid rgba(217,119,87,0.28); }
  .message-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
  .message-role { font-size: 11px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
  .message-meta { color: var(--muted); font-size: 11px; text-align: right; }
  .message-body { white-space: pre-wrap; line-height: 1.7; word-break: break-word; font-size: 13px; }
  .message-activity-group { display: flex; flex-direction: column; gap: 8px; align-items: center; }
  .message-activity { width: min(720px, 100%); background: rgba(255,255,255,0.03); border: 1px dashed var(--border); border-radius: 12px; padding: 10px 12px; }
  .message-activity-label { color: var(--accent); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .message-activity-body { color: var(--muted); line-height: 1.6; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }

  footer { border-top: 1px solid var(--border); padding: 20px 24px; margin-top: 8px; }
  .footer-content { max-width: 1400px; margin: 0 auto; }
  .footer-content p { color: var(--muted); font-size: 12px; line-height: 1.7; margin-bottom: 4px; }
  .footer-content p:last-child { margin-bottom: 0; }
  .footer-content a { color: var(--blue); text-decoration: none; }
  .footer-content a:hover { text-decoration: underline; }

  @media (max-width: 768px) {
    .charts-grid { grid-template-columns: 1fr; }
    .chart-card.wide { grid-column: 1; }
    .session-overview { grid-template-columns: 1fr 1fr; }
    .session-summary { grid-template-columns: 1fr; }
    .session-modal-backdrop { padding: 12px; }
    .session-modal { max-height: 94vh; border-radius: 14px; }
    .message-card { max-width: 100%; }
  }
</style>
</head>
<body>
<header>
  <h1>Claude Code Usage Dashboard</h1>
  <div class="meta" id="meta">Loading...</div>
</header>

<div id="filter-bar">
  <div class="filter-label">Models</div>
  <div id="model-checkboxes"></div>
  <button class="filter-btn" onclick="selectAllModels()">All</button>
  <button class="filter-btn" onclick="clearAllModels()">None</button>
  <div class="filter-sep"></div>
  <div class="filter-label">Range</div>
  <div class="range-group">
    <button class="range-btn" data-range="7d"  onclick="setRange('7d')">7d</button>
    <button class="range-btn" data-range="30d" onclick="setRange('30d')">30d</button>
    <button class="range-btn" data-range="90d" onclick="setRange('90d')">90d</button>
    <button class="range-btn" data-range="all" onclick="setRange('all')">All</button>
  </div>
</div>

<div class="container">
  <div class="stats-row" id="stats-row"></div>
  <div class="charts-grid">
    <div class="chart-card wide">
      <h2 id="daily-chart-title">Daily Token Usage</h2>
      <div class="chart-wrap tall"><canvas id="chart-daily"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>By Model</h2>
      <div class="chart-wrap"><canvas id="chart-model"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>Top Projects by Tokens</h2>
      <div class="chart-wrap"><canvas id="chart-project"></canvas></div>
    </div>
  </div>
  <div class="table-card">
    <div class="section-title">Recent Sessions</div>
    <table class="sessions-table">
      <thead><tr>
        <th>Session</th><th>Project</th><th>Last Active</th><th>Duration</th>
        <th>Model</th><th>Turns</th><th>Input</th><th>Output</th><th>Est. Cost</th>
      </tr></thead>
      <tbody id="sessions-body"></tbody>
    </table>
  </div>
  <div class="table-card">
    <div class="section-title">Cost by Model</div>
    <table>
      <thead><tr>
        <th>Model</th><th>Turns</th><th>Input</th><th>Output</th>
        <th>Cache Read</th><th>Cache Creation</th><th>Est. Cost</th>
      </tr></thead>
      <tbody id="model-cost-body"></tbody>
    </table>
  </div>
</div>

<div id="session-modal-backdrop" class="session-modal-backdrop" onclick="onSessionModalBackdrop(event)">
  <div class="session-modal" role="dialog" aria-modal="true" aria-labelledby="session-modal-title">
    <div class="session-modal-header">
      <div>
        <div id="session-modal-title" class="session-modal-title">Session Context</div>
        <div id="session-modal-subtitle" class="session-modal-subtitle">Open a session from the table to inspect its transcript.</div>
      </div>
      <div class="session-modal-controls">
        <button class="session-detail-close" onclick="closeSessionDetail()">Close</button>
      </div>
    </div>
    <div id="session-detail" class="session-modal-body">
      <div class="session-detail-empty">Click a session row to inspect the transcript context from its source `.jsonl` file.</div>
    </div>
  </div>
</div>

<footer>
  <div class="footer-content">
    <p>Cost estimates based on Anthropic API pricing (<a href="https://claude.com/pricing#api" target="_blank">claude.com/pricing#api</a>) as of April 2026. Only models containing <em>opus</em>, <em>sonnet</em>, or <em>haiku</em> in the name are included in cost calculations. Actual costs for Max/Pro subscribers differ from API pricing.</p>
    <p>
      GitHub: <a href="https://github.com/phuryn/claude-usage" target="_blank">https://github.com/phuryn/claude-usage</a>
      &nbsp;&middot;&nbsp;
      Created by: <a href="https://www.productcompass.pm" target="_blank">The Product Compass Newsletter</a>
      &nbsp;&middot;&nbsp;
      License: MIT
    </p>
  </div>
</footer>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let rawData = null;
let selectedModels = new Set();
let selectedRange = '30d';
let activeSessionId = null;
let hoverPreviewSessionId = null;
let hoverPreviewData = null;
let hoverPreviewTimer = null;
let previewCache = new Map();
let visibleSessions = [];
let charts = {};

// ── Pricing (Anthropic API, April 2026) ────────────────────────────────────
const PRICING = {
  'claude-opus-4-6':   { input: 6.15,  output: 30.75, cache_write: 7.69, cache_read: 0.61 },
  'claude-opus-4-5':   { input: 6.15,  output: 30.75, cache_write: 7.69, cache_read: 0.61 },
  'claude-sonnet-4-6': { input: 3.69,  output: 18.45, cache_write: 4.61, cache_read: 0.37 },
  'claude-sonnet-4-5': { input: 3.69,  output: 18.45, cache_write: 4.61, cache_read: 0.37 },
  'claude-haiku-4-5':  { input: 1.23,  output:  6.15, cache_write: 1.54, cache_read: 0.12 },
  'claude-haiku-4-6':  { input: 1.23,  output:  6.15, cache_write: 1.54, cache_read: 0.12 },
};

function isBillable(model) {
  if (!model) return false;
  const m = model.toLowerCase();
  return m.includes('opus') || m.includes('sonnet') || m.includes('haiku');
}

function getPricing(model) {
  if (!model) return null;
  if (PRICING[model]) return PRICING[model];
  for (const key of Object.keys(PRICING)) {
    if (model.startsWith(key)) return PRICING[key];
  }
  const m = model.toLowerCase();
  if (m.includes('opus'))   return PRICING['claude-opus-4-6'];
  if (m.includes('sonnet')) return PRICING['claude-sonnet-4-6'];
  if (m.includes('haiku'))  return PRICING['claude-haiku-4-5'];
  return null;
}

function calcCost(model, inp, out, cacheRead, cacheCreation) {
  if (!isBillable(model)) return 0;
  const p = getPricing(model);
  if (!p) return 0;
  return (
    inp           * p.input       / 1e6 +
    out           * p.output      / 1e6 +
    cacheRead     * p.cache_read  / 1e6 +
    cacheCreation * p.cache_write / 1e6
  );
}

// ── Formatting ─────────────────────────────────────────────────────────────
function fmt(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return n.toLocaleString();
}
function fmtCost(c)    { return '$' + c.toFixed(4); }
function fmtCostBig(c) { return '$' + c.toFixed(2); }

// ── Chart colors ───────────────────────────────────────────────────────────
const TOKEN_COLORS = {
  input:          'rgba(79,142,247,0.8)',
  output:         'rgba(167,139,250,0.8)',
  cache_read:     'rgba(74,222,128,0.6)',
  cache_creation: 'rgba(251,191,36,0.6)',
};
const MODEL_COLORS = ['#d97757','#4f8ef7','#4ade80','#a78bfa','#fbbf24','#f472b6','#34d399','#60a5fa'];

// ── Time range ─────────────────────────────────────────────────────────────
const RANGE_LABELS = { '7d': 'Last 7 Days', '30d': 'Last 30 Days', '90d': 'Last 90 Days', 'all': 'All Time' };
const RANGE_TICKS  = { '7d': 7, '30d': 15, '90d': 13, 'all': 12 };

function getRangeCutoff(range) {
  if (range === 'all') return null;
  const days = range === '7d' ? 7 : range === '30d' ? 30 : 90;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function readURLRange() {
  const p = new URLSearchParams(window.location.search).get('range');
  return ['7d', '30d', '90d', 'all'].includes(p) ? p : '30d';
}

function readURLSession() {
  return new URLSearchParams(window.location.search).get('session') || null;
}

function setRange(range) {
  selectedRange = range;
  document.querySelectorAll('.range-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.range === range)
  );
  updateURL();
  applyFilter();
}

// ── Model filter ───────────────────────────────────────────────────────────
function modelPriority(m) {
  const ml = m.toLowerCase();
  if (ml.includes('opus'))   return 0;
  if (ml.includes('sonnet')) return 1;
  if (ml.includes('haiku'))  return 2;
  return 3;
}

function readURLModels(allModels) {
  const param = new URLSearchParams(window.location.search).get('models');
  if (!param) return new Set(allModels.filter(m => isBillable(m)));
  const fromURL = new Set(param.split(',').map(s => s.trim()).filter(Boolean));
  return new Set(allModels.filter(m => fromURL.has(m)));
}

function isDefaultModelSelection(allModels) {
  const billable = allModels.filter(m => isBillable(m));
  if (selectedModels.size !== billable.length) return false;
  return billable.every(m => selectedModels.has(m));
}

function buildFilterUI(allModels) {
  const sorted = [...allModels].sort((a, b) => {
    const pa = modelPriority(a), pb = modelPriority(b);
    return pa !== pb ? pa - pb : a.localeCompare(b);
  });
  selectedModels = readURLModels(allModels);
  const container = document.getElementById('model-checkboxes');
  container.innerHTML = sorted.map(m => {
    const checked = selectedModels.has(m);
    return `<label class="model-cb-label ${checked ? 'checked' : ''}" data-model="${m}">
      <input type="checkbox" value="${m}" ${checked ? 'checked' : ''} onchange="onModelToggle(this)">
      ${m}
    </label>`;
  }).join('');
}

function onModelToggle(cb) {
  const label = cb.closest('label');
  if (cb.checked) { selectedModels.add(cb.value);    label.classList.add('checked'); }
  else            { selectedModels.delete(cb.value); label.classList.remove('checked'); }
  updateURL();
  applyFilter();
}

function selectAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = true; selectedModels.add(cb.value); cb.closest('label').classList.add('checked');
  });
  updateURL(); applyFilter();
}

function clearAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = false; selectedModels.delete(cb.value); cb.closest('label').classList.remove('checked');
  });
  updateURL(); applyFilter();
}

// ── URL persistence ────────────────────────────────────────────────────────
function updateURL() {
  const allModels = Array.from(document.querySelectorAll('#model-checkboxes input')).map(cb => cb.value);
  const params = new URLSearchParams();
  if (selectedRange !== '30d') params.set('range', selectedRange);
  if (!isDefaultModelSelection(allModels)) params.set('models', Array.from(selectedModels).join(','));
  if (activeSessionId) params.set('session', activeSessionId);
  const search = params.toString() ? '?' + params.toString() : '';
  history.replaceState(null, '', window.location.pathname + search);
}

// ── Aggregation & filtering ────────────────────────────────────────────────
function applyFilter() {
  if (!rawData) return;

  const cutoff = getRangeCutoff(selectedRange);

  // Filter daily rows by model + date range
  const filteredDaily = rawData.daily_by_model.filter(r =>
    selectedModels.has(r.model) && (!cutoff || r.day >= cutoff)
  );

  // Daily chart: aggregate by day
  const dailyMap = {};
  for (const r of filteredDaily) {
    if (!dailyMap[r.day]) dailyMap[r.day] = { day: r.day, input: 0, output: 0, cache_read: 0, cache_creation: 0 };
    const d = dailyMap[r.day];
    d.input          += r.input;
    d.output         += r.output;
    d.cache_read     += r.cache_read;
    d.cache_creation += r.cache_creation;
  }
  const daily = Object.values(dailyMap).sort((a, b) => a.day.localeCompare(b.day));

  // By model: aggregate tokens + turns from daily data
  const modelMap = {};
  for (const r of filteredDaily) {
    if (!modelMap[r.model]) modelMap[r.model] = { model: r.model, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0 };
    const m = modelMap[r.model];
    m.input          += r.input;
    m.output         += r.output;
    m.cache_read     += r.cache_read;
    m.cache_creation += r.cache_creation;
    m.turns          += r.turns;
  }

  // Filter sessions by model + date range
  const filteredSessions = rawData.sessions_all.filter(s =>
    selectedModels.has(s.model) && (!cutoff || s.last_date >= cutoff)
  );

  // Add session counts into modelMap
  for (const s of filteredSessions) {
    if (modelMap[s.model]) modelMap[s.model].sessions++;
  }

  const byModel = Object.values(modelMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // By project: aggregate from filtered sessions
  const projMap = {};
  for (const s of filteredSessions) {
    if (!projMap[s.project]) projMap[s.project] = { project: s.project, input: 0, output: 0, turns: 0 };
    projMap[s.project].input  += s.input;
    projMap[s.project].output += s.output;
    projMap[s.project].turns  += s.turns;
  }
  const byProject = Object.values(projMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // Totals
  const totals = {
    sessions:       filteredSessions.length,
    turns:          byModel.reduce((s, m) => s + m.turns, 0),
    input:          byModel.reduce((s, m) => s + m.input, 0),
    output:         byModel.reduce((s, m) => s + m.output, 0),
    cache_read:     byModel.reduce((s, m) => s + m.cache_read, 0),
    cache_creation: byModel.reduce((s, m) => s + m.cache_creation, 0),
    cost:           byModel.reduce((s, m) => s + calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation), 0),
  };

  // Update daily chart title
  document.getElementById('daily-chart-title').textContent = 'Daily Token Usage \u2014 ' + RANGE_LABELS[selectedRange];
  visibleSessions = filteredSessions.slice(0, 20);

  renderStats(totals);
  renderDailyChart(daily);
  renderModelChart(byModel);
  renderProjectChart(byProject);
  renderSessionsTable(visibleSessions);
  renderModelCostTable(byModel);
}

function rerenderSessionsOnly() {
  if (!rawData) return;
  renderSessionsTable(visibleSessions);
}

// ── Renderers ──────────────────────────────────────────────────────────────
function renderStats(t) {
  const rangeLabel = RANGE_LABELS[selectedRange].toLowerCase();
  const stats = [
    { label: 'Sessions',       value: t.sessions.toLocaleString(), sub: rangeLabel },
    { label: 'Turns',          value: fmt(t.turns),                sub: rangeLabel },
    { label: 'Input Tokens',   value: fmt(t.input),                sub: rangeLabel },
    { label: 'Output Tokens',  value: fmt(t.output),               sub: rangeLabel },
    { label: 'Cache Read',     value: fmt(t.cache_read),           sub: 'from prompt cache' },
    { label: 'Cache Creation', value: fmt(t.cache_creation),       sub: 'writes to prompt cache' },
    { label: 'Est. Cost',      value: fmtCostBig(t.cost),          sub: 'API pricing, Apr 2026', color: '#4ade80' },
  ];
  document.getElementById('stats-row').innerHTML = stats.map(s => `
    <div class="stat-card">
      <div class="label">${s.label}</div>
      <div class="value" style="${s.color ? 'color:' + s.color : ''}">${s.value}</div>
      ${s.sub ? `<div class="sub">${s.sub}</div>` : ''}
    </div>
  `).join('');
}

function renderDailyChart(daily) {
  const ctx = document.getElementById('chart-daily').getContext('2d');
  if (charts.daily) charts.daily.destroy();
  charts.daily = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: daily.map(d => d.day),
      datasets: [
        { label: 'Input',          data: daily.map(d => d.input),          backgroundColor: TOKEN_COLORS.input,          stack: 'tokens' },
        { label: 'Output',         data: daily.map(d => d.output),         backgroundColor: TOKEN_COLORS.output,         stack: 'tokens' },
        { label: 'Cache Read',     data: daily.map(d => d.cache_read),     backgroundColor: TOKEN_COLORS.cache_read,     stack: 'tokens' },
        { label: 'Cache Creation', data: daily.map(d => d.cache_creation), backgroundColor: TOKEN_COLORS.cache_creation, stack: 'tokens' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8892a4', boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: '#8892a4', maxTicksLimit: RANGE_TICKS[selectedRange] }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#8892a4', callback: v => fmt(v) }, grid: { color: '#2a2d3a' } },
      }
    }
  });
}

function renderModelChart(byModel) {
  const ctx = document.getElementById('chart-model').getContext('2d');
  if (charts.model) charts.model.destroy();
  if (!byModel.length) { charts.model = null; return; }
  charts.model = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: byModel.map(m => m.model),
      datasets: [{ data: byModel.map(m => m.input + m.output), backgroundColor: MODEL_COLORS, borderWidth: 2, borderColor: '#1a1d27' }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#8892a4', boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)} tokens` } }
      }
    }
  });
}

function renderProjectChart(byProject) {
  const top = byProject.slice(0, 10);
  const ctx = document.getElementById('chart-project').getContext('2d');
  if (charts.project) charts.project.destroy();
  if (!top.length) { charts.project = null; return; }
  charts.project = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: top.map(p => p.project.length > 22 ? '\u2026' + p.project.slice(-20) : p.project),
      datasets: [
        { label: 'Input',  data: top.map(p => p.input),  backgroundColor: TOKEN_COLORS.input },
        { label: 'Output', data: top.map(p => p.output), backgroundColor: TOKEN_COLORS.output },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8892a4', boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: '#8892a4', callback: v => fmt(v) }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#8892a4', font: { size: 11 } }, grid: { color: '#2a2d3a' } },
      }
    }
  });
}

function renderSessionsTable(sessions) {
  document.getElementById('sessions-body').innerHTML = sessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
    const costCell = isBillable(s.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    const isActive = s.session_id === activeSessionId;
    const showPreview = hoverPreviewSessionId === s.session_id;
    return `<tr class="session-row-hover ${isActive ? 'active' : ''}" onclick="openSessionDetail('${s.session_id}')" onmouseenter="scheduleSessionPreview('${s.session_id}')" onmouseleave="hideSessionPreview()">
      <td>
        <div class="session-link">
          <div class="session-link-id">${s.session_short}&hellip;</div>
          <div class="session-link-meta">Open transcript</div>
          ${showPreview ? renderSessionHoverCard() : ''}
        </div>
      </td>
      <td>${s.project}</td>
      <td class="muted">${s.last}</td>
      <td class="muted">${s.duration_min}m</td>
      <td><span class="model-tag">${s.model}</span></td>
      <td class="num">${s.turns}</td>
      <td class="num">${fmt(s.input)}</td>
      <td class="num">${fmt(s.output)}</td>
      ${costCell}
    </tr>`;
  }).join('');
}

function escapeHTML(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function renderSessionHoverCard() {
  if (!hoverPreviewSessionId) return '';
  if (!hoverPreviewData) {
    return `<div class="session-hover-card">
      <div class="session-hover-kicker">Session Preview</div>
      <div class="session-hover-body">Loading summary…</div>
    </div>`;
  }
  if (hoverPreviewData.error) {
    return `<div class="session-hover-card">
      <div class="session-hover-kicker">Session Preview</div>
      <div class="session-hover-body">${escapeHTML(hoverPreviewData.error)}</div>
    </div>`;
  }
  const preview = hoverPreviewData;
  const pills = [
    preview.project ? `Project ${preview.project}` : '',
    preview.model ? `Model ${preview.model}` : '',
    preview.message_count ? `${preview.message_count} messages` : '',
    preview.token_total ? `${fmt(preview.token_total)} tokens` : '',
  ].filter(Boolean).map(item => `<div class="session-hover-pill">${escapeHTML(item)}</div>`).join('');
  return `<div class="session-hover-card">
    <div class="session-hover-kicker">Session Preview</div>
    <div class="session-hover-title">${escapeHTML(preview.first_user_excerpt || `Session ${preview.session_short}…`)}</div>
    <div class="session-hover-meta">${pills}</div>
    <div class="session-hover-body">Hover to scan intent. Click to open the full transcript.</div>
  </div>`;
}

function scheduleSessionPreview(sessionId) {
  clearTimeout(hoverPreviewTimer);
  hoverPreviewSessionId = sessionId;
  hoverPreviewData = previewCache.get(sessionId) || null;
  rerenderSessionsOnly();
  if (previewCache.has(sessionId)) return;
  hoverPreviewTimer = setTimeout(() => loadSessionPreview(sessionId), 180);
}

function hideSessionPreview() {
  clearTimeout(hoverPreviewTimer);
  hoverPreviewSessionId = null;
  hoverPreviewData = null;
  rerenderSessionsOnly();
}

async function loadSessionPreview(sessionId) {
  if (previewCache.has(sessionId)) {
    hoverPreviewData = previewCache.get(sessionId);
    rerenderSessionsOnly();
    return;
  }
  try {
    const resp = await fetch('/api/session_preview?session_id=' + encodeURIComponent(sessionId));
    const data = await resp.json();
    previewCache.set(sessionId, data);
    if (hoverPreviewSessionId === sessionId) {
      hoverPreviewData = data;
      rerenderSessionsOnly();
    }
  } catch (e) {
    const errorData = { error: 'Failed to load summary.' };
    previewCache.set(sessionId, errorData);
    if (hoverPreviewSessionId === sessionId) {
      hoverPreviewData = errorData;
      rerenderSessionsOnly();
    }
  }
}

function renderSessionDetailLoading() {
  document.getElementById('session-modal-title').textContent = 'Session Context';
  document.getElementById('session-modal-subtitle').textContent = 'Loading transcript…';
  document.getElementById('session-detail').innerHTML = '<div class="session-detail-empty">Loading session context…</div>';
  document.getElementById('session-modal-backdrop').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function renderSessionDetailError(message) {
  document.getElementById('session-modal-title').textContent = 'Session Context';
  document.getElementById('session-modal-subtitle').textContent = 'There was a problem loading this transcript.';
  document.getElementById('session-detail').innerHTML = `<div class="session-detail-error">${escapeHTML(message)}</div>`;
  document.getElementById('session-modal-backdrop').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeSessionDetail() {
  activeSessionId = null;
  updateURL();
  applyFilter();
  document.getElementById('session-modal-backdrop').classList.remove('open');
  document.body.style.overflow = '';
  document.getElementById('session-modal-title').textContent = 'Session Context';
  document.getElementById('session-modal-subtitle').textContent = 'Open a session from the table to inspect its transcript.';
  document.getElementById('session-detail').innerHTML = '<div class="session-detail-empty">Click a session row to inspect the transcript context from its source `.jsonl` file.</div>';
}

function onSessionModalBackdrop(event) {
  if (event.target.id === 'session-modal-backdrop') closeSessionDetail();
}

function humanizeActivity(text) {
  if (text.startsWith('[tool_use]')) return { label: 'Tool Call', body: text.replace('[tool_use]', '').trim() };
  if (text.startsWith('[tool_result]')) return { label: 'Tool Result', body: text.replace('[tool_result]', '').trim() };
  return null;
}

function summarizeSessionTitle(summary, meta) {
  const text = (summary.first_user_excerpt || '').trim();
  if (!text) return meta.project || `Session ${meta.session_short}`;
  return text.length > 72 ? text.slice(0, 72).trimEnd() + '…' : text;
}

function formatSessionDate(value) {
  if (!value) return 'Unknown';
  return value.replace('T', ' ').replace('Z', '').slice(0, 16);
}

function renderSessionDetail(data) {
  const meta = data.session;
  const summary = data.summary || {};
  const sessionTitle = summarizeSessionTitle(summary, meta);
  const messageHTML = data.messages.map(msg => {
    const activity = humanizeActivity(msg.content);
    const tokenParts = [];
    if (msg.role === 'assistant') {
      tokenParts.push(`in ${fmt(msg.input_tokens)}`);
      tokenParts.push(`out ${fmt(msg.output_tokens)}`);
      if (msg.cache_read_tokens) tokenParts.push(`cache read ${fmt(msg.cache_read_tokens)}`);
      if (msg.cache_creation_tokens) tokenParts.push(`cache write ${fmt(msg.cache_creation_tokens)}`);
    }
    if (activity) {
      return `<div class="message-activity-group">
        <div class="message-activity">
          <div class="message-activity-label">${escapeHTML(activity.label)}</div>
          <div class="message-activity-body">${escapeHTML(activity.body)}</div>
        </div>
      </div>`;
    }
    return `<div class="message-card ${msg.role}">
      <div class="message-head">
        <div class="message-role">${msg.role}${msg.model ? ' · ' + escapeHTML(msg.model) : ''}</div>
        <div class="message-meta">${escapeHTML((msg.timestamp || '').replace('T', ' ').slice(0, 19))}${tokenParts.length ? ' · ' + escapeHTML(tokenParts.join(' · ')) : ''}</div>
      </div>
      <div class="message-body">${escapeHTML(msg.content)}</div>
    </div>`;
  }).join('');

  const metaPills = [
    meta.project ? `Project: ${meta.project}` : '',
    meta.git_branch ? `Branch: ${meta.git_branch}` : '',
    summary.message_count ? `Messages: ${summary.message_count}` : '',
    summary.assistant_count ? `Assistant replies: ${summary.assistant_count}` : '',
    meta.source_file ? `File: ${meta.source_file}` : '',
  ].filter(Boolean).map(item => `<div class="session-detail-pill">${escapeHTML(item)}</div>`).join('');

  const assistantMessages = data.messages.filter(msg => msg.role === 'assistant');
  const primaryModel = assistantMessages.find(msg => msg.model)?.model || 'unknown';
  const totalInput = assistantMessages.reduce((sum, msg) => sum + (msg.input_tokens || 0), 0);
  const totalOutput = assistantMessages.reduce((sum, msg) => sum + (msg.output_tokens || 0), 0);
  const totalCacheRead = assistantMessages.reduce((sum, msg) => sum + (msg.cache_read_tokens || 0), 0);
  const totalCacheCreation = assistantMessages.reduce((sum, msg) => sum + (msg.cache_creation_tokens || 0), 0);
  const estimatedCost = isBillable(primaryModel)
    ? fmtCost(calcCost(primaryModel, totalInput, totalOutput, totalCacheRead, totalCacheCreation))
    : 'n/a';
  const overviewCards = [
    { label: 'Project', value: meta.project || 'Unknown', sub: meta.git_branch ? `Branch ${meta.git_branch}` : 'No branch captured' },
    { label: 'Timeline', value: formatSessionDate(meta.last_timestamp), sub: `Started ${formatSessionDate(meta.first_timestamp)}` },
    { label: 'Model', value: primaryModel, sub: `${summary.assistant_count || 0} assistant replies` },
    { label: 'Usage', value: `${fmt(totalInput + totalOutput)} tokens`, sub: `Cost ${estimatedCost}` },
  ].map(card => `
    <div class="session-overview-card">
      <div class="session-overview-label">${escapeHTML(card.label)}</div>
      <div class="session-overview-value">${escapeHTML(card.value)}</div>
      <div class="session-overview-sub">${escapeHTML(card.sub)}</div>
    </div>
  `).join('');

  document.getElementById('session-modal-title').textContent = sessionTitle;
  document.getElementById('session-modal-subtitle').textContent = `Session ${meta.session_short}…`;
  document.getElementById('session-detail').innerHTML = `
    <div class="session-hero">
      <div class="session-kicker">Conversation Focus</div>
      <div class="session-hero-title">${escapeHTML(sessionTitle)}</div>
      <div class="session-hero-text">${summary.first_user_excerpt ? escapeHTML(summary.first_user_excerpt) : 'No user prompt text available for this session.'}</div>
    </div>
    <div class="session-overview">${overviewCards}</div>
    <div class="session-summary">
      <div class="session-summary-card">
        <div class="session-summary-label">Session Metadata</div>
        <div class="session-detail-meta">${metaPills}</div>
      </div>
      <div class="session-summary-card">
        <div class="session-summary-label">Reading Note</div>
        <div class="session-summary-text">User and assistant messages are shown as the main conversation. Tool calls and tool results are separated so the session reads more like a narrative than a raw log.</div>
      </div>
    </div>
    <div class="transcript-section-title">Transcript</div>
    <div class="message-list">${messageHTML}</div>
  `;
  document.getElementById('session-modal-backdrop').classList.add('open');
  document.body.style.overflow = 'hidden';
}

async function openSessionDetail(sessionId) {
  activeSessionId = sessionId;
  updateURL();
  applyFilter();
  renderSessionDetailLoading();
  try {
    const resp = await fetch('/api/session?session_id=' + encodeURIComponent(sessionId));
    const data = await resp.json();
    if (data.error) {
      renderSessionDetailError(data.error);
      return;
    }
    renderSessionDetail(data);
  } catch (e) {
    console.error(e);
    renderSessionDetailError('Failed to load session context.');
  }
}

function renderModelCostTable(byModel) {
  document.getElementById('model-cost-body').innerHTML = byModel.map(m => {
    const cost = calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation);
    const costCell = isBillable(m.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td><span class="model-tag">${m.model}</span></td>
      <td class="num">${fmt(m.turns)}</td>
      <td class="num">${fmt(m.input)}</td>
      <td class="num">${fmt(m.output)}</td>
      <td class="num">${fmt(m.cache_read)}</td>
      <td class="num">${fmt(m.cache_creation)}</td>
      ${costCell}
    </tr>`;
  }).join('');
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadData() {
  try {
    const resp = await fetch('/api/data');
    const d = await resp.json();
    if (d.error) {
      document.body.innerHTML = '<div style="padding:40px;color:#f87171">' + d.error + '</div>';
      return;
    }
    document.getElementById('meta').textContent = 'Updated: ' + d.generated_at + ' \u00b7 Auto-refresh in 30s';

    const isFirstLoad = rawData === null;
    rawData = d;

    if (isFirstLoad) {
      // Restore range from URL, mark active button
      selectedRange = readURLRange();
      activeSessionId = readURLSession();
      document.querySelectorAll('.range-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.range === selectedRange)
      );
      // Build model filter (reads URL for model selection too)
      buildFilterUI(d.all_models);
    }

    applyFilter();
    if (isFirstLoad && activeSessionId) openSessionDetail(activeSessionId);
  } catch(e) {
    console.error(e);
  }
}

loadData();
setInterval(loadData, 30000);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && activeSessionId) closeSessionDetail();
});
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

        elif parsed.path == "/api/data":
            data = get_dashboard_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/session":
            session_id = parse_qs(parsed.query).get("session_id", [""])[0].strip()
            if not session_id:
                body = json.dumps({"error": "Missing session_id"}).encode("utf-8")
                self.send_response(400)
            else:
                body = json.dumps(get_session_detail(session_id)).encode("utf-8")
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/session_preview":
            session_id = parse_qs(parsed.query).get("session_id", [""])[0].strip()
            if not session_id:
                body = json.dumps({"error": "Missing session_id"}).encode("utf-8")
                self.send_response(400)
            else:
                body = json.dumps(get_session_preview(session_id)).encode("utf-8")
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


def serve(port=8080):
    server = HTTPServer(("localhost", port), DashboardHandler)
    print(f"Dashboard running at http://localhost:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    serve()
