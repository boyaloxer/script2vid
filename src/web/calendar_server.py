"""
script2vid Web Server — Pipeline launcher + Release calendar GUI.

Serves two views at http://localhost:5555:
  /           Pipeline UI (create videos from scripts)
  /calendar   Release calendar (view/manage scheduled slots)

REST API endpoints power both frontends.

Usage:
    python -m src.web.calendar_server              # starts on port 5555
    python -m src.web.calendar_server --port 8080
"""

import argparse
import json
import re
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from src.publishing import calendar_manager as cm
from src.web import pipeline_runner as runner
from src.web import dashboard_api

_PORT = 5555
_STATIC_DIR = Path(__file__).resolve().parent / "static"


# ════════════════════════════════════════════════════════════════════
#  Static file helpers
# ════════════════════════════════════════════════════════════════════

def _load_html(filename: str) -> str:
    """Load an HTML file from the static directory."""
    return (_STATIC_DIR / filename).read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════════
#  HTTP Handler
# ════════════════════════════════════════════════════════════════════

class AppHandler(BaseHTTPRequestHandler):
    """Route requests to page views or API endpoints."""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[s2v-web] {args[0]}\n")

    # ── Helpers ───────────────────────────────────────────────────

    def _send_json(self, data: dict | list, status: int = 200):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    def _path_parts(self) -> tuple[str, dict]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/"), parse_qs(parsed.query)

    # ── GET ───────────────────────────────────────────────────────

    def do_GET(self):
        path, qs = self._path_parts()
        # ── Pages ─────────────────────────────────────────────────
        if path == "" or path == "/":
            self._send_html(_load_html("pipeline.html"))
        elif path == "/calendar":
            self._send_html(_load_html("calendar.html"))
        elif path == "/dashboard":
            self._send_html(_load_html("dashboard.html"))

        # ── Dashboard API ─────────────────────────────────────────
        elif path == "/api/dashboard/overview":
            ch = qs.get("channel", [None])[0]
            self._send_json(dashboard_api.get_overview(ch))
        elif path == "/api/dashboard/memory":
            ch = qs.get("channel", [None])[0]
            self._send_json(dashboard_api.get_memory(ch))
        elif path == "/api/dashboard/experiments":
            ch = qs.get("channel", [None])[0]
            self._send_json(dashboard_api.get_experiments(ch))
        elif path == "/api/dashboard/intelligence":
            ch = qs.get("channel", [None])[0]
            self._send_json(dashboard_api.get_intelligence(ch))
        elif path == "/api/dashboard/sessions":
            self._send_json(dashboard_api.get_recent_sessions())
        elif path == "/api/dashboard/dataset":
            self._send_json(dashboard_api.get_dataset_stats())
        elif path == "/api/dashboard/optimizations":
            ch = qs.get("channel", [None])[0]
            self._send_json(dashboard_api.get_optimizations(ch))

        # ── Live Activity Feed ────────────────────────────────────
        elif path == "/api/dashboard/activity":
            since = int(qs.get("since", [0])[0])
            try:
                from src.agent.activity_feed import get_since
                self._send_json(get_since(since))
            except Exception as e:
                self._send_json({"seq": 0, "entries": [], "error": str(e)})

        # ── Command Queue (recent) ───────────────────────────────
        elif path == "/api/dashboard/commands":
            try:
                from src.agent.command_queue import get_recent
                self._send_json({"commands": get_recent()})
            except Exception as e:
                self._send_json({"commands": [], "error": str(e)})

        # ── Calendar API ──────────────────────────────────────────
        elif path == "/api/calendar":
            self._send_json(cm.load_calendar())
        elif path == "/api/channels":
            self._send_json(cm.list_channels())
        elif path == "/api/slots":
            cal = cm.load_calendar()
            slots = cal["slots"]
            ch = qs.get("channel", [None])[0]
            if ch:
                slots = [s for s in slots if s["channel_id"] == ch]
            self._send_json(slots)

        # ── Channel defaults API ──────────────────────────────────
        elif re.match(r"^/api/channels/[^/]+/defaults$", path):
            channel_id = path.split("/")[3]
            defaults = runner.get_channel_defaults(channel_id)
            self._send_json(defaults)

        # ── Pipeline API ──────────────────────────────────────────
        elif path == "/api/pipeline/jobs":
            self._send_json(runner.list_jobs())
        elif re.match(r"^/api/pipeline/jobs/[^/]+$", path):
            job_id = path.split("/")[-1]
            job = runner.get_job(job_id)
            if job:
                self._send_json(job)
            else:
                self._send_json({"error": "Job not found"}, 404)

        else:
            self.send_error(404)

    # ── POST ──────────────────────────────────────────────────────

    def do_POST(self):
        path, _ = self._path_parts()

        # ── Calendar: add channel ─────────────────────────────────
        if path == "/api/channels":
            body = self._read_body()
            try:
                result = cm.add_channel(
                    channel_id=body["channel_id"],
                    name=body["name"],
                    days=body["days"],
                    time_str=body["time"],
                    timezone=body.get("timezone", "America/New_York"),
                    category=body.get("category", "people"),
                    tags=body.get("tags", []),
                )
                self._send_json(result, 201)
            except (KeyError, ValueError) as e:
                self._send_json({"error": str(e)}, 400)

        # ── Calendar: generate slots ──────────────────────────────
        elif path == "/api/slots/generate":
            body = self._read_body()
            new = cm.generate_slots(
                channel_id=body.get("channel_id"),
                weeks=body.get("weeks", 4),
            )
            self._send_json({"created": len(new), "slots": new})

        # ── Calendar: publish due ─────────────────────────────────
        elif path == "/api/publish-due":
            body = self._read_body()
            results = cm.publish_due(hours_ahead=body.get("hours", 48))
            self._send_json(results)

        # ── Pipeline: start job ───────────────────────────────────
        elif path == "/api/pipeline/start":
            body = self._read_body()
            try:
                job = runner.start_job(
                    channel=body["channel"],
                    script_text=body["script_text"],
                    title=body.get("title"),
                    description=body.get("description"),
                    overrides=body.get("overrides"),
                )
                self._send_json(job, 201)
            except (KeyError, ValueError) as e:
                self._send_json({"error": str(e)}, 400)

        # ── Dashboard: send command ───────────────────────────────
        elif path == "/api/dashboard/command":
            body = self._read_body()
            text = body.get("text", "").strip()
            if not text:
                self._send_json({"error": "Empty command"}, 400)
            else:
                try:
                    from src.agent.command_queue import push_command
                    from src.agent.activity_feed import emit as feed_emit
                    cmd = push_command(text, source="dashboard")
                    feed_emit("info", f"User command: {text}", action="user_command")
                    self._send_json(cmd, 201)
                except Exception as e:
                    self._send_json({"error": str(e)}, 500)

        # ── Dashboard: chat with agent ────────────────────────────
        elif path == "/api/dashboard/chat":
            body = self._read_body()
            message = body.get("message", "").strip()
            channel = body.get("channel")
            if not message:
                self._send_json({"error": "Empty message"}, 400)
            else:
                try:
                    from src.agent.agent_chat import agent_reply
                    from src.agent.activity_feed import emit as feed_emit
                    feed_emit("info", f"Operator: {message}", action="user_chat")
                    print(f"[s2v-chat] Calling agent_reply(channel={channel!r})")
                    reply = agent_reply(message, channel_id=channel)
                    print(f"[s2v-chat] Got reply: {reply[:80]!r}")
                    feed_emit("think", f"Agent: {reply[:200]}", action="agent_reply")
                    self._send_json({"reply": reply})
                except Exception as e:
                    import traceback
                    print(f"[s2v-chat] ERROR: {e}")
                    traceback.print_exc()
                    self._send_json({"error": str(e)}, 500)

        # ── Pipeline: cancel job ──────────────────────────────────
        elif re.match(r"^/api/pipeline/jobs/[^/]+/cancel$", path):
            job_id = path.split("/")[4]
            ok = runner.cancel_job(job_id)
            self._send_json({"cancelled": ok})

        else:
            self.send_error(404)

    # ── PUT ───────────────────────────────────────────────────────

    def do_PUT(self):
        path, _ = self._path_parts()

        if path.startswith("/api/slots/"):
            slot_id = path.split("/")[-1]
            body = self._read_body()
            result = cm.update_slot(slot_id, **body)
            if result:
                self._send_json(result)
            else:
                self._send_json({"error": "Slot not found"}, 404)

        elif re.match(r"^/api/channels/[^/]+$", path):
            channel_id = path.split("/")[-1]
            body = self._read_body()
            try:
                result = cm.add_channel(
                    channel_id=channel_id,
                    name=body.get("name", channel_id),
                    days=body.get("days", []),
                    time_str=body.get("time", "12:00"),
                    timezone=body.get("timezone", "America/New_York"),
                    category=body.get("category", "people"),
                    tags=body.get("tags", []),
                )
                self._send_json(result)
            except (KeyError, ValueError) as e:
                self._send_json({"error": str(e)}, 400)
        else:
            self.send_error(404)

    # ── DELETE ────────────────────────────────────────────────────

    def do_DELETE(self):
        path, _ = self._path_parts()

        if path.startswith("/api/channels/"):
            channel_id = path.split("/")[-1]
            ok = cm.remove_channel(channel_id)
            self._send_json({"deleted": ok})

        elif path.startswith("/api/slots/"):
            slot_id = path.split("/")[-1]
            ok = cm.delete_slot(slot_id)
            self._send_json({"deleted": ok})

        else:
            self.send_error(404)


# ════════════════════════════════════════════════════════════════════
#  Server start
# ════════════════════════════════════════════════════════════════════

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a new thread so the browser can't block the API."""
    daemon_threads = True
    allow_reuse_address = True


def start_server(port: int = _PORT) -> None:
    """Start the web UI and open it in the default browser."""
    server = ThreadedHTTPServer(("127.0.0.1", port), AppHandler)
    url = f"http://localhost:{port}"
    print(f"[script2vid] Web UI at {url}")
    print(f"[script2vid] Pipeline:  {url}/")
    print(f"[script2vid] Calendar:  {url}/calendar")
    print(f"[script2vid] Dashboard: {url}/dashboard")
    print("[script2vid] Press Ctrl+C to stop.\n")
    webbrowser.open(f"{url}/dashboard")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[script2vid] Server stopped.")
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="script2vid Web UI")
    parser.add_argument("--port", type=int, default=_PORT,
                        help=f"Port to run the server on (default: {_PORT}).")
    args = parser.parse_args()
    start_server(port=args.port)


if __name__ == "__main__":
    main()
