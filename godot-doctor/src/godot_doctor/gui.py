from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
import webbrowser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .gui_service import GuiError, GuiService

CONTENT_TYPES = {
    ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, initial_path: str = ".", port: int = 0):
        self.service = GuiService(initial_path)
        self.session = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), GuiHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.cookie_name = f"godot_doctor_{self.server_port}"


class GuiHandler(BaseHTTPRequestHandler):
    server: GuiServer
    server_version = "GodotDoctor"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(self, status: int, data: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), usb=(), payment=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=True).encode("utf-8"), "application/json")

    def _authorize(self, api: bool) -> None:
        expected_host = urlsplit(self.server.origin).netloc
        if self.headers.get_all("Host", []) != [expected_host]:
            raise GuiError("This server accepts its local loopback address only.", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            raise GuiError("Cross-origin requests are not allowed.", 403)
        if not api:
            return
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise GuiError("Cross-site requests are not allowed.", 403)
        if self.headers.get("X-Godot-Doctor") != "1":
            raise GuiError("Open the local Godot Doctor interface to use this API.", 403)
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            supplied = cookies[self.server.cookie_name].value if self.server.cookie_name in cookies else ""
            authorized = hmac.compare_digest(supplied, self.server.session)
        except (CookieError, TypeError):
            authorized = False
        if not authorized:
            raise GuiError("The local session is missing or expired. Reload the interface.", 403)

    @staticmethod
    def _integer(query: dict[str, list[str]], key: str, default: str = "0") -> int:
        try:
            return int(query.get(key, [default])[0])
        except ValueError as exc:
            raise GuiError(f"{key} must be an integer.") from exc

    def do_GET(self) -> None:
        try:
            url = urlsplit(self.path)
            self._authorize(url.path.startswith("/api/"))
            query = parse_qs(url.query, keep_blank_values=True)
            if url.path == "/api/config":
                initial, home = self.server.service.initial_path, str(Path.home())
                roots = [{"label": "Starting location", "path": initial}]
                if home != initial:
                    roots.append({"label": "Home", "path": home})
                roots.append({"label": "Filesystem", "path": Path(home).anchor})
                self._json(200, {
                    "version": __version__, "initial_path": initial,
                    "platform": sys.platform, "roots": roots,
                })
            elif url.path == "/api/status":
                self._json(200, self.server.service.snapshot())
            elif url.path == "/api/report":
                text, mime, name = self.server.service.export(
                    query.get("format", ["json"])[0], self._integer(query, "run_id"),
                )
                self._send(200, text.encode("utf-8", errors="backslashreplace"), mime, {
                    "Content-Disposition": f'attachment; filename="{name}"',
                })
            else:
                self._asset(url.path)
        except GuiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except OSError as exc:
            self._json(404, {"error": f"File unavailable: {exc}"})

    def _asset(self, url_path: str) -> None:
        name = "index.html" if url_path == "/" else url_path.removeprefix("/static/")
        if url_path != "/" and not url_path.startswith("/static/"):
            raise GuiError("Not found.", 404)
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise GuiError("Not found.", 404)
        mime = CONTENT_TYPES.get(Path(name).suffix)
        asset = files("godot_doctor").joinpath("static", name)
        if mime is None or not asset.is_file():
            raise GuiError("Not found.", 404)
        headers = {}
        if url_path == "/":
            headers["Set-Cookie"] = (
                f"{self.server.cookie_name}={self.server.session}; Path=/; HttpOnly; SameSite=Strict"
            )
        self._send(200, asset.read_bytes(), mime, headers)

    def do_POST(self) -> None:
        try:
            self._authorize(True)
            if self.headers.get_content_type() != "application/json":
                raise GuiError("Use application/json for this request.", 415)
            try:
                size = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise GuiError("Invalid request length.") from exc
            if not 0 <= size <= 16384 or self.headers.get("Transfer-Encoding"):
                raise GuiError("Request body must be at most 16 KiB with a fixed length.", 413)
            try:
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise GuiError("Invalid JSON request.") from exc
            if not isinstance(payload, dict):
                raise GuiError("The request must be a JSON object.")
            path = urlsplit(self.path).path
            if path == "/api/analyze":
                self._json(202, self.server.service.start(payload.get("path")))
            elif path == "/api/browse":
                self._json(200, self.server.service.browse(payload.get("path")))
            elif path == "/api/source":
                file = payload.get("file")
                line, run_id = payload.get("line", 1), payload.get("run_id", 0)
                if not isinstance(file, str) or len(file) > 4096 or "\x00" in file:
                    raise GuiError("Source file must be a valid path string.")
                if type(line) is not int or type(run_id) is not int:
                    raise GuiError("Source line and run_id must be integers.")
                self._json(200, self.server.service.source(file, line, run_id))
            elif path == "/api/shutdown":
                self._json(200, {"stopped": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                raise GuiError("Not found.", 404)
        except GuiError as exc:
            self._json(exc.status, {"error": str(exc)})

    def do_OPTIONS(self) -> None:
        self._json(403, {"error": "Cross-origin API access is disabled."})


def _watch_stdin(server: GuiServer) -> None:
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return

    def watch() -> None:
        try:
            while stream.read(4096):
                pass
        except (OSError, ValueError):
            pass
        finally:
            server.shutdown()

    threading.Thread(target=watch, name="godot-doctor-stdin-watch", daemon=True).start()


def serve_gui(
    initial_path: str = ".",
    port: int = 0,
    open_browser: bool = True,
    exit_on_stdin_close: bool = False,
) -> int:
    try:
        server = GuiServer(initial_path, port)
    except (OSError, ValueError) as exc:
        print(f"godot-doctor: cannot start local GUI: {exc}", file=sys.stderr)
        return 2
    print(f"Godot Doctor GUI: {server.origin}", flush=True)
    print("Local, read-only analysis. Press Ctrl+C to stop the server.", flush=True)
    if exit_on_stdin_close:
        _watch_stdin(server)
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(server.origin,), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nGodot Doctor stopped.")
    finally:
        server.server_close()
    return 0
