"""
dashboard.py - Local web dashboard served on localhost:8080.
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from store import DB_PATH, connect
from collectors.common import discover_accounts
import queries


WEB_DIR = Path(__file__).with_name('web')
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


def get_dashboard_data(db_path=DB_PATH, **filters):
    if not Path(db_path).exists():
        return {"error": "Database not found. Run: python cli.py scan"}
    conn = connect(db_path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone():
            return {"error": "This database predates the event model. Run: python cli.py migrate"}
        data = queries.overview(conn, **filters)
        data.update(
            last_scan=datetime.fromtimestamp(Path(db_path).stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        return data
    finally:
        conn.close()


def run_scan(db_path=DB_PATH):
    from scanner import scan
    result = scan(db_path=db_path, verbose=False)
    return dict(events_added=result['events_added'], files_read=result['new'] + result['updated'],
                mismatches=len(result['reconciliation']['mismatches']))


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


def find_session_file(session_id, projects_dirs=None):
    if projects_dirs is None:
        projects_dirs = [path for _, path in discover_accounts()]
    all_jsonl = [p for d in projects_dirs for p in d.glob("**/*.jsonl")]

    for path in all_jsonl:
        if path.stem == session_id:
            return path

    for path in all_jsonl:
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if session_id not in line:
                        continue
                    try:
                        rec = json.loads(line)
                        if rec.get("sessionId") == session_id:
                            return path
                    except json.JSONDecodeError:
                        pass
        except OSError:
            continue
    return None


def get_session_detail(session_id):
    if session_id.startswith(("codex:", "gemini:")):
        return {"error": "Preview not available for this tool (" + session_id.split(":", 1)[0] + ")."}
    path = find_session_file(session_id)
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

    from collectors.claude import collect
    usage_summary = queries.aggregate([e for e in collect(path) if e['session_id'] == session_id])
    usage_summary = {k: v for k, v in usage_summary.items() if not k.startswith('_')}
    return {
        "usage": usage_summary,
        "session": session_meta,
        "summary": {
            "message_count": len(messages),
            "assistant_count": assistant_count,
            "first_user_excerpt": (first_user_message[:180] + "…") if len(first_user_message) > 180 else first_user_message,
        },
        "messages": messages,
    }


def get_session_preview(session_id):
    detail = get_session_detail(session_id)
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


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in STATIC:
            name, content_type = STATIC[parsed.path]
            body = (WEB_DIR / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/data":
            params = parse_qs(parsed.query)
            filters = {key: params[key][0] for key in ('start', 'end', 'source', 'account', 'project')
                       if params.get(key, [''])[0]}
            if params.get('models', [''])[0]:
                filters['models'] = params['models'][0].split(',')
            data = get_dashboard_data(**filters)
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

    def do_POST(self):
        if urlparse(self.path).path != "/api/scan":
            self.send_response(404)
            self.end_headers()
            return
        try:
            body, status = json.dumps(run_scan()).encode("utf-8"), 200
        except Exception as exc:  # report the failure to the page instead of dropping the connection
            body, status = json.dumps({"error": str(exc)}).encode("utf-8"), 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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
