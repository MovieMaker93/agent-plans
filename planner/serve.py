# SPDX-License-Identifier: Apache-2.0
"""Read-only HTTP server for the generated dashboard.

Bind it to localhost and put Tailscale Serve in front of it. The process
does not open a public URL by itself. GET and HEAD only. `/regenerate`
rewrites the generated views from plan.yaml. It does not edit plans.
"""

from __future__ import annotations

import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from planner.util import plan_dirs

ROUTES = {
    "/": "dashboard/index.html",
    "/index.html": "dashboard/index.html",
    "/dashboard": "dashboard/index.html",
    "/dashboard/": "dashboard/index.html",
    "/dashboard/index.html": "dashboard/index.html",
    "/dashboard/index.md": "dashboard/index.md",
    "/dashboard/digest.md": "dashboard/digest.md",
    "/digest": "dashboard/digest.md",
    "/digest.md": "dashboard/digest.md",
    "/dashboard/slack-payload.json": "dashboard/slack-payload.json",
    "/slack-payload.json": "dashboard/slack-payload.json",
    "/portfolio.md": "portfolio.md",
    "/portfolio": "portfolio.md",
    "/portfolio.yaml": "portfolio.yaml",
    "/portfolio-schedule.yaml": "portfolio-schedule.yaml",
}

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".yaml": "text/yaml; charset=utf-8",
    ".yml": "text/yaml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

NAV = (
    '<nav class="bot-ui">'
    '<a href="/">Dashboard</a> '
    '<a href="/dashboard/digest.md">Digest</a> '
    '<a href="/portfolio.md">Portfolio</a> '
    '<a href="/portfolio.yaml">Portfolio YAML</a> '
    '<a href="/portfolio-schedule.yaml">Shared schedule</a> '
    '<a href="/regenerate">Regenerate</a>'
    "</nav>"
)
NAV_STYLE = (
    "<style>nav.bot-ui{font-family:ui-monospace,monospace;font-size:0.85rem;"
    "margin:0 0 1rem}nav.bot-ui a{margin-right:0.8rem}</style>"
)


class Route:
    def __init__(self, kind: str, relpath: str | None = None):
        self.kind = kind
        self.relpath = relpath


def route(method: str, target: str) -> Route:
    if method not in {"GET", "HEAD"}:
        return Route("method")
    path = unquote(urlsplit(target).path)
    if any(part == ".." for part in path.split("/")):
        return Route("missing")
    if path.rstrip("/") == "/regenerate":
        return Route("regenerate")
    relpath = ROUTES.get(path)
    if relpath is None:
        return Route("missing")
    return Route("file", relpath)


def content_type(relpath: str) -> str:
    suffix = Path(relpath).suffix.lower()
    return TYPES.get(suffix, "application/octet-stream")


def inject_html(body: bytes, refresh_seconds: int | None) -> bytes:
    """Add the bot-ui nav in the response only. The file on disk stays as generated."""
    text = body.decode("utf-8")
    extra = NAV_STYLE
    if refresh_seconds and refresh_seconds > 0:
        extra += f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
    if "<head>" in text:
        text = text.replace("<head>", "<head>" + extra, 1)
    if "<body>" in text:
        text = text.replace("<body>", "<body>" + NAV, 1)
    return text.encode("utf-8")


def read_served(root: Path, relpath: str, refresh_seconds: int | None) -> tuple[bytes, str] | None:
    base = root.resolve()
    path = (base / relpath).resolve()
    if path != base and base not in path.parents:
        return None
    if not path.is_file():
        return None
    body = path.read_bytes()
    if relpath.endswith(".html"):
        body = inject_html(body, refresh_seconds)
    return body, content_type(relpath)


def regenerate(root: Path, at: datetime) -> None:
    """Rewrite dashboard, digest, and the Slack payload from the live plans."""
    from planner.cli import _open_roster, _scheduled
    from planner.dashboard import write_dashboard, write_digest
    from planner.slack import write_payload

    roster = _open_roster(root)
    if roster is None:
        raise ValueError("roster failed to load")
    directories = plan_dirs(root, include_templates=False)
    if not directories:
        raise ValueError("no plans found")
    plans = []
    results = {}
    for directory in directories:
        plan, result = _scheduled(directory, roster)
        if plan is None or result is None:
            raise ValueError(f"plan failed: {directory}")
        plans.append(plan)
        results[plan.plan_id] = result
    write_dashboard(root, plans, roster, results, at)
    _path, text = write_digest(root, plans, roster, results, at)
    write_payload(root, text)


def _handler_class():
    class Handler(BaseHTTPRequestHandler):
        server_version = "planner-dashboard"

        def log_message(self, fmt: str, *args) -> None:
            # Local access log only. No request body, so a webhook cannot leak here.
            super().log_message(fmt, *args)

        def do_HEAD(self) -> None:
            self._respond(include_body=False)

        def do_GET(self) -> None:
            self._respond(include_body=True)

        def do_POST(self) -> None:
            self._method_not_allowed()

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def _method_not_allowed(self) -> None:
            body = b"method not allowed\n"
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _respond(self, include_body: bool) -> None:
            decision = route(self.command if include_body else "GET", self.path)
            if self.command == "HEAD":
                decision = route("GET", self.path)
            if decision.kind == "method":
                self._method_not_allowed()
                return
            if decision.kind == "regenerate":
                try:
                    regenerate(self.server.root, self.server.clock())
                except ValueError as exc:
                    body = f"regenerate failed: {exc}\n".encode("utf-8")
                    self._send(500, "text/plain; charset=utf-8", body, include_body)
                    return
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if decision.kind != "file" or decision.relpath is None:
                self._send(404, "text/plain; charset=utf-8", b"not found\n", include_body)
                return
            served = read_served(
                self.server.root, decision.relpath, self.server.refresh_seconds
            )
            if served is None:
                self._send(404, "text/plain; charset=utf-8", b"not found\n", include_body)
                return
            body, kind = served
            self._send(200, kind, body, include_body)

        def _send(self, status: int, kind: str, body: bytes, include_body: bool) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if include_body:
                self.wfile.write(body)

    return Handler


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, address, root: Path, refresh_seconds: int | None, clock):
        self.root = root
        self.refresh_seconds = refresh_seconds
        self.clock = clock
        super().__init__(address, _handler_class())
        self.daemon_threads = True


def serve(
    root: Path,
    host: str,
    port: int,
    *,
    refresh_seconds: int | None = None,
    at: datetime | None = None,
) -> None:
    """Block until interrupted. Prints the local URL and the Tailscale command."""

    def clock() -> datetime:
        if at is not None:
            return at
        return datetime.now().astimezone().replace(microsecond=0)

    stop = threading.Event()
    if refresh_seconds:
        regenerate(root, clock())

        def _loop() -> None:
            while not stop.wait(refresh_seconds):
                try:
                    regenerate(root, clock())
                except ValueError:
                    continue

        threading.Thread(target=_loop, name="planner-refresh", daemon=True).start()
    httpd = DashboardServer((host, port), root.resolve(), refresh_seconds, clock)
    print(f"serving read-only dashboard at http://{host}:{port}/")
    print("pages: /  /dashboard/digest.md  /portfolio.md  /portfolio.yaml  /portfolio-schedule.yaml")
    print("regenerate: GET /regenerate  (rewrites generated views, leaves plan.yaml alone)")
    print(f"tailscale: tailscale serve --bg {port}")
    print("That exposes the localhost port on your tailnet only. No public funnel is required.")
    if refresh_seconds:
        print(f"refresh: every {refresh_seconds}s, and the HTML response asks the browser to reload")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        stop.set()
        httpd.server_close()
