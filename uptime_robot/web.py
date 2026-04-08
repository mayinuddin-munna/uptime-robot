from __future__ import annotations

import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .storage import Storage


def render_dashboard(storage: Storage) -> str:
    summary = storage.get_summary()
    incidents = storage.get_incidents(limit=10)

    cards = "\n".join(
        f"""
        <article class=\"card {'down' if item['status'] == 'down' else 'up'}\">
            <div class=\"card-top\">
                <h2>{html.escape(item['name'])}</h2>
                <span class=\"badge\">{html.escape(item['status'].upper())}</span>
            </div>
            <p class=\"url\">{html.escape(item['url'])}</p>
            <dl>
                <div><dt>Last Check</dt><dd>{html.escape(item['last_checked_at'] or 'Never')}</dd></div>
                <div><dt>Status Code</dt><dd>{item['last_status_code'] or '-'}</dd></div>
                <div><dt>Latency</dt><dd>{item['last_latency_ms'] or '-'} ms</dd></div>
                <div><dt>24h Uptime</dt><dd>{item['uptime_24h']}%</dd></div>
                <div><dt>Failures</dt><dd>{item['consecutive_failures']}</dd></div>
            </dl>
            <p class=\"error\">{html.escape(item['last_error'] or 'Healthy')}</p>
        </article>
        """.strip()
        for item in summary
    ) or "<p>No monitors configured.</p>"

    incident_rows = "\n".join(
        f"<tr><td>{html.escape(item['monitor_name'])}</td><td>{html.escape(item['started_at'])}</td><td>{html.escape(item['resolved_at'] or 'Open')}</td><td>{html.escape(item['status'])}</td><td>{html.escape(item['summary'])}</td></tr>"
        for item in incidents
    ) or "<tr><td colspan='5'>No incidents recorded.</td></tr>"

    return f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Uptime Robot</title>
<style>
:root {{
  --bg: #08111f;
  --panel: rgba(9, 25, 45, 0.88);
  --panel-border: rgba(144, 202, 249, 0.18);
  --text: #e6f2ff;
  --muted: #8ea7c4;
  --up: #23c483;
  --down: #ff6b57;
  --accent: #49c6e5;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: "Segoe UI", sans-serif;
  color: var(--text);
  background:
    radial-gradient(circle at top left, rgba(73,198,229,0.18), transparent 28%),
    radial-gradient(circle at bottom right, rgba(255,107,87,0.16), transparent 22%),
    linear-gradient(160deg, #050b14 0%, #08111f 48%, #10203a 100%);
}}
main {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 48px; }}
header {{ display: flex; justify-content: space-between; align-items: end; gap: 16px; flex-wrap: wrap; margin-bottom: 28px; }}
h1 {{ margin: 0; font-size: clamp(2rem, 4vw, 3.4rem); letter-spacing: 0.04em; text-transform: uppercase; }}
.subtitle {{ margin: 8px 0 0; color: var(--muted); max-width: 720px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px; }}
.card {{ background: var(--panel); border: 1px solid var(--panel-border); border-radius: 18px; padding: 18px; backdrop-filter: blur(10px); box-shadow: 0 24px 60px rgba(0, 0, 0, 0.28); }}
.card.up {{ border-color: rgba(35,196,131,0.22); }}
.card.down {{ border-color: rgba(255,107,87,0.35); }}
.card-top {{ display: flex; justify-content: space-between; gap: 10px; align-items: start; }}
.badge {{ font-size: 0.75rem; font-weight: 700; border-radius: 999px; padding: 6px 10px; background: rgba(73,198,229,0.16); }}
.url {{ color: var(--accent); word-break: break-all; }}
dl {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 0; }}
dt {{ color: var(--muted); font-size: 0.8rem; margin-bottom: 4px; }}
dd {{ margin: 0; font-weight: 600; }}
.error {{ margin-top: 14px; color: #ffd9d4; min-height: 1.4em; }}
.section {{ margin-top: 28px; background: var(--panel); border: 1px solid var(--panel-border); border-radius: 18px; padding: 20px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid rgba(255,255,255,0.08); font-size: 0.95rem; vertical-align: top; }}
small {{ color: var(--muted); }}
@media (max-width: 720px) {{ dl {{ grid-template-columns: 1fr; }} th:nth-child(3), td:nth-child(3) {{ display: none; }} }}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Uptime Robot</h1>
      <p class="subtitle">Config-driven uptime monitoring for internal services, public endpoints, APIs, and operational dependencies.</p>
    </div>
    <small>JSON API: /api/status, /api/checks, /api/incidents</small>
  </header>
  <section class="grid">{cards}</section>
  <section class="section">
    <h2>Recent Incidents</h2>
    <table>
      <thead><tr><th>Monitor</th><th>Started</th><th>Resolved</th><th>Status</th><th>Summary</th></tr></thead>
      <tbody>{incident_rows}</tbody>
    </table>
  </section>
</main>
</body>
</html>
"""


class UptimeRequestHandler(BaseHTTPRequestHandler):
    storage: Storage

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            html = render_dashboard(self.storage)
            self._send_text(html, content_type="text/html; charset=utf-8")
            return
        if parsed.path == "/healthz":
            self._send_json({"status": "ok"})
            return
        if parsed.path == "/api/status":
            self._send_json(self.storage.get_summary())
            return
        if parsed.path == "/api/checks":
            params = parse_qs(parsed.query)
            limit = _read_limit(params, default=100, maximum=500)
            monitor = params.get("monitor", [None])[0]
            if monitor:
                self._send_json(self.storage.get_checks_for_monitor(monitor, limit=limit))
            else:
                self._send_json(self.storage.get_recent_checks(limit=limit))
            return
        if parsed.path == "/api/incidents":
            params = parse_qs(parsed.query)
            limit = _read_limit(params, default=50, maximum=200)
            self._send_json(self.storage.get_incidents(limit=limit))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, payload: str, content_type: str, status: int = 200) -> None:
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str, port: int, storage: Storage) -> ThreadingHTTPServer:
    handler = type("BoundUptimeRequestHandler", (UptimeRequestHandler,), {"storage": storage})
    return ThreadingHTTPServer((host, port), handler)


def _read_limit(params: dict[str, list[str]], default: int, maximum: int) -> int:
    try:
        value = int(params.get("limit", [str(default)])[0])
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))
