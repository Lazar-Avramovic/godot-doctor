from __future__ import annotations

import http.client
import io
import json
import os
import re
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from godot_doctor.cli import main
from godot_doctor.gui import GuiServer, _watch_stdin, serve_gui
from godot_doctor.gui_service import GuiError, GuiService
from godot_doctor.report import format_json
from godot_doctor.project import Project
from godot_doctor.rules import analyze

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class GuiServiceTests(unittest.TestCase):
    def scan(self, service: GuiService, path: Path | str) -> dict:
        started = service.start(str(path))
        self.assertEqual(started["status"], "running")
        service._thread.join(timeout=5)
        self.assertFalse(service._thread.is_alive())
        return service.snapshot()

    def test_clean_and_broken_reports_match_the_cli(self) -> None:
        service = GuiService()
        for name in ("clean", "broken"):
            with self.subTest(name=name):
                path = FIXTURES / name
                state = self.scan(service, path)
                self.assertEqual(state["status"], "complete")
                self.assertEqual(state["report"], json.loads(format_json(analyze(Project.load(path)))))
                self.assertIsNotNone(state["finished_at"])
                self.assertGreaterEqual(state["elapsed_ms"], 0)

    def test_failed_scan_clears_previous_results_and_can_recover(self) -> None:
        service = GuiService()
        self.scan(service, FIXTURES / "broken")
        failed = self.scan(service, FIXTURES / "does-not-exist")
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["report"])
        self.assertIn("does not exist", failed["error"])
        with self.assertRaises(GuiError):
            service.export("json", failed["run_id"])
        self.assertEqual(self.scan(service, FIXTURES / "clean")["report"]["diagnostics"], [])

    def test_invalid_inputs_are_rejected(self) -> None:
        service = GuiService()
        for path in (None, "", " ", [], {}, "bad\x00path", "a" * 4097):
            with self.subTest(path=type(path).__name__), self.assertRaises(GuiError):
                service.start(path)

    def test_quoted_or_padded_paths_are_accepted(self) -> None:
        service = GuiService()
        state = self.scan(service, f'  "{FIXTURES / "clean"}"  ')
        self.assertEqual(state["status"], "complete")
        self.assertEqual(Path(state["path"]), (FIXTURES / "clean").resolve())

    def test_related_shader_files_are_previewable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            shaders = root / "shaders"
            shaders.mkdir()
            (shaders / "wave.gdshader").write_text(
                "shader_type canvas_item;\n", encoding="utf-8",
            )
            (root / "main.tscn").write_text(
                '[gd_scene format=3]\n'
                '[ext_resource type="Shader" path="res://shaders/Wave.gdshader" id="1"]\n',
                encoding="utf-8",
            )
            service = GuiService()
            state = self.scan(service, root)
            self.assertEqual(state["status"], "complete")
            diagnostic = next(
                row for row in state["report"]["diagnostics"]
                if row["rule"] == "case-mismatch"
            )
            source = service.source(diagnostic["related"][0], 1, state["run_id"])
            self.assertEqual(source["lines"][0]["text"], "shader_type canvas_item;")

    def test_fix_preview_apply_and_rescan_match_the_cli(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "project.godot"
            config.write_text(
                'config_version=5\n# build note\nconfig/name="Fixable"\n',
                encoding="utf-8",
            )
            service = GuiService()
            state = self.scan(service, root)
            self.assertEqual(state["report"]["summary"]["errors"], 1)
            run_id = state["run_id"]

            preview = service.fix(run_id, False)
            self.assertFalse(preview["applied"])
            self.assertEqual(preview["summary"]["lines"], 1)
            self.assertIn("# build note", config.read_text(encoding="utf-8"))

            applied = service.fix(run_id, True)
            self.assertTrue(applied["applied"])
            self.assertIn("; build note", config.read_text(encoding="utf-8"))
            self.assertEqual(service.snapshot()["status"], "idle")
            with self.assertRaises(GuiError) as error:
                service.export("json", run_id)
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(self.scan(service, root)["report"]["diagnostics"], [])

    def test_fix_requires_a_current_complete_run_and_typed_arguments(self) -> None:
        service = GuiService()
        with self.assertRaises(GuiError) as error:
            service.fix(0, False)
        self.assertEqual(error.exception.status, 409)
        for run_id, apply in (("1", False), (1, "yes"), (True, False)):
            with self.subTest(run_id=run_id, apply=apply), self.assertRaises(GuiError) as bad:
                service.fix(run_id, apply)
            self.assertEqual(bad.exception.status, 400)
        state = self.scan(service, FIXTURES / "clean")
        with self.assertRaises(GuiError) as stale:
            service.fix(state["run_id"] + 1, False)
        self.assertEqual(stale.exception.status, 409)
        with self.assertRaises(GuiError) as preview_first:
            service.fix(state["run_id"], True)
        self.assertEqual(preview_first.exception.status, 409)
        self.assertIn("Preview the fix", str(preview_first.exception))

    def test_fix_apply_refuses_a_file_changed_after_the_preview(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "project.godot"
            config.write_text("config_version=5\n# note\n", encoding="utf-8")
            service = GuiService()
            run_id = self.scan(service, root)["run_id"]
            service.fix(run_id, False)
            config.write_text("config_version=5\n# newer note\n", encoding="utf-8")
            with self.assertRaises(GuiError) as error:
                service.fix(run_id, True)
            self.assertEqual(error.exception.status, 409)
            self.assertIn("changed since the preview", str(error.exception))
            self.assertEqual(config.read_text(encoding="utf-8"), "config_version=5\n# newer note\n")
            self.assertEqual(service.snapshot()["status"], "complete")

    def test_only_one_scan_can_run_at_a_time(self) -> None:
        service = GuiService()
        entered, release = threading.Event(), threading.Event()
        original = analyze

        def slow_analyze(project):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(project)

        with patch("godot_doctor.gui_service.analyze", side_effect=slow_analyze):
            service.start(str(FIXTURES / "clean"))
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(GuiError) as error:
                    service.start(str(FIXTURES / "broken"))
                self.assertEqual(error.exception.status, 409)
                self.assertEqual(service.snapshot()["status"], "running")
                self.assertIsNone(service.snapshot()["report"])
            finally:
                release.set()
                service._thread.join(timeout=5)
        self.assertEqual(service.snapshot()["status"], "complete")

    def test_source_previews_and_exports_are_scoped_to_current_report(self) -> None:
        service = GuiService()
        state = self.scan(service, FIXTURES / "broken")
        run_id = state["run_id"]
        source = service.source("broken.gd", 3, run_id)
        self.assertEqual(source["line"], 3)
        self.assertTrue(any(row["number"] == 3 and "preload" in row["text"] for row in source["lines"]))
        text, mime, name = service.export("json", run_id)
        self.assertEqual(json.loads(text), state["report"])
        self.assertEqual((mime, name), ("application/json", "godot-doctor-report.json"))
        text, mime, name = service.export("text", run_id)
        self.assertIn("[broken-reference]", text)
        self.assertEqual(name, "godot-doctor-report.txt")
        for name in ("../../outside.gd", "not-in-report.gd"):
            with self.subTest(name=name), self.assertRaises(GuiError):
                service.source(name, 1, run_id)
        with self.assertRaises(GuiError):
            service.source("broken.gd", 0, run_id)
        with self.assertRaises(GuiError):
            service.export("html", run_id)
        self.scan(service, FIXTURES / "broken")
        with self.assertRaises(GuiError) as error:
            service.source("broken.gd", 3, run_id)
        self.assertEqual(error.exception.status, 409)

    def test_source_preview_cannot_follow_an_out_of_root_mapping(self) -> None:
        service = GuiService()
        state = self.scan(service, FIXTURES / "broken")
        service._result.project.files["broken.gd"] = FIXTURES / "clean" / "main.gd"
        with self.assertRaises(GuiError) as error:
            service.source("broken.gd", 1, state["run_id"])
        self.assertEqual(error.exception.status, 403)

    def test_directory_picker_handles_spaces_and_unicode_without_recursing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "Example ž project"
            project.mkdir()
            (project / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            (root / "not-a-folder.txt").write_text("", encoding="utf-8")
            listing = GuiService().browse(str(root))
            self.assertEqual(listing["path"], str(root.resolve()))
            self.assertEqual(listing["directories"], [{
                "name": project.name, "path": str(project), "is_project": True,
            }])
            self.assertTrue(GuiService().browse(str(project / "project.godot"))["is_project"])
            with self.assertRaises(GuiError):
                GuiService().browse(str(root / "missing"))

    def test_inaccessible_child_does_not_hide_accessible_directories(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            private, public = root / "private", root / "public"
            private.mkdir()
            public.mkdir()
            original = Path.is_file

            def guarded_is_file(path):
                if path == private / "project.godot":
                    raise PermissionError("private directory")
                return original(path)

            with patch.object(Path, "is_file", guarded_is_file):
                listing = GuiService().browse(str(root))
            self.assertIn("public", [row["name"] for row in listing["directories"]])
            blocked = next(row for row in listing["directories"] if row["name"] == "private")
            self.assertFalse(blocked["accessible"])
            self.assertEqual(listing["unreadable_count"], 1)


class GuiHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = GuiServer(str(FIXTURES))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        worker = self.server.service._thread
        if worker:
            worker.join(timeout=5)

    def request(self, method: str, path: str, payload=None, headers=None, authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {}
        if authenticated:
            request_headers.update({
                "Cookie": f"{self.server.cookie_name}={self.server.session}",
                "X-Godot-Doctor": "1",
            })
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_loopback_binding_and_authenticated_status(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        code, headers, body = self.request("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["status"], "idle")
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            headers["Permissions-Policy"],
            "camera=(), microphone=(), geolocation=(), usb=(), payment=()",
        )
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_missing_cookie_or_custom_header_is_denied(self) -> None:
        cases = ({}, {"X-Godot-Doctor": "1"}, {"Cookie": f"{self.server.cookie_name}={self.server.session}"})
        for headers in cases:
            with self.subTest(headers=list(headers)):
                code, _, _ = self.request("GET", "/api/status", headers=headers, authenticated=False)
                self.assertEqual(code, 403)

    def test_cross_origin_and_rebinding_requests_are_denied(self) -> None:
        for headers in (
            {"Origin": "https://untrusted.invalid"},
            {"Origin": "null"},
            {"Host": "untrusted.invalid"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                code, _, _ = self.request("GET", "/api/config", headers=headers)
                self.assertEqual(code, 403)
        self.assertEqual(self.request("OPTIONS", "/api/analyze")[0], 403)
        self.assertEqual(self.request("GET", "/api/config", headers={"Origin": self.server.origin})[0], 200)

    def test_config_roots_start_at_the_launch_location(self) -> None:
        code, _, body = self.request("GET", "/api/config")
        self.assertEqual(code, 200)
        config = json.loads(body)
        self.assertEqual(config["initial_path"], str(FIXTURES))
        self.assertEqual(
            config["roots"][0],
            {"label": "Starting location", "path": str(FIXTURES)},
        )
        self.assertEqual(len(config["rules"]), 15)
        for rule in config["rules"]:
            self.assertTrue(rule["description"])
            self.assertTrue(rule["remediation"])
            self.assertIn(rule["severity"], ("error", "warning", "info"))

    def test_api_scan_and_export(self) -> None:
        code, _, body = self.request("POST", "/api/analyze", {"path": str(FIXTURES / "broken")})
        self.assertEqual(code, 202)
        run_id = json.loads(body)["run_id"]
        self.server.service._thread.join(timeout=5)
        code, _, body = self.request("GET", "/api/status")
        state = json.loads(body)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["report"]["summary"]["errors"], 6)
        code, headers, body = self.request("GET", f"/api/report?format=json&run_id={run_id}")
        self.assertEqual(code, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(json.loads(body), state["report"])
        code, _, body = self.request("POST", "/api/source", {"file": "broken.gd", "line": 3, "run_id": run_id})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["line"], 3)

    def test_undecodable_linux_filename_bytes_survive_json_responses(self) -> None:
        payload = {"path": "/tmp/bad\udcff", "directories": [{"name": "bad\udcff"}]}
        with patch.object(self.server.service, "browse", return_value=payload) as browse:
            code, _, body = self.request("POST", "/api/browse", {"path": payload["path"]})
            browse.assert_called_once_with(payload["path"])
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), payload)
        body.decode("utf-8")

    def test_reports_escape_undecodable_filename_bytes(self) -> None:
        self.server.service.start(str(FIXTURES / "broken"))
        self.server.service._thread.join(timeout=5)
        result = self.server.service._result
        result.diagnostics[0] = replace(result.diagnostics[0], file="bad\udcff.gd")
        run_id = self.server.service.snapshot()["run_id"]
        code, _, body = self.request("GET", f"/api/report?format=json&run_id={run_id}")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["diagnostics"][0]["file"], "bad\udcff.gd")
        code, _, body = self.request("GET", f"/api/report?format=text&run_id={run_id}")
        self.assertEqual(code, 200)
        self.assertIn(r"bad\udcff.gd", body.decode("utf-8"))

    def test_bad_request_bodies_and_unknown_routes_are_rejected(self) -> None:
        self.assertEqual(self.request("POST", "/api/analyze", ["invalid"])[0], 400)
        self.assertEqual(self.request("POST", "/api/analyze", {"path": ""})[0], 400)
        self.assertEqual(self.request("POST", "/api/analyze", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/analyze", {"path": "x" * 17000})[0], 413)
        self.assertEqual(self.request("POST", "/api/source", {"file": "x.gd", "line": "bad"})[0], 400)
        self.assertEqual(self.request("GET", "/not-a-route")[0], 404)
        self.assertEqual(self.request("GET", "/static/../../gui.py")[0], 404)

    def test_api_fix_previews_and_applies_the_current_run(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "project.godot"
            config.write_text("config_version=5\n# note\n", encoding="utf-8")
            code, _, body = self.request("POST", "/api/analyze", {"path": str(root)})
            self.assertEqual(code, 202)
            self.server.service._thread.join(timeout=5)
            run_id = self.server.service.snapshot()["run_id"]

            code, _, body = self.request(
                "POST", "/api/fix", {"run_id": run_id, "apply": False},
            )
            self.assertEqual(code, 200)
            preview = json.loads(body)
            self.assertFalse(preview["applied"])
            self.assertEqual(preview["summary"]["lines"], 1)
            self.assertIn("# note", config.read_text(encoding="utf-8"))

            for payload in (
                {"run_id": "1", "apply": False},
                {"run_id": run_id, "apply": "yes"},
            ):
                with self.subTest(payload=payload):
                    self.assertEqual(
                        self.request("POST", "/api/fix", payload)[0], 400,
                    )

            code, _, body = self.request(
                "POST", "/api/fix", {"run_id": run_id, "apply": True},
            )
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)["applied"])
            self.assertIn("; note", config.read_text(encoding="utf-8"))
            self.assertEqual(
                self.request("POST", "/api/fix", {"run_id": run_id, "apply": False})[0],
                409,
            )

    def test_shutdown_requires_an_authenticated_session(self) -> None:
        self.assertEqual(self.request("POST", "/api/shutdown", {}, authenticated=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/shutdown", {})[0], 200)
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def test_page_bootstrap_and_static_assets_are_packaged(self) -> None:
        code, headers, body = self.request("GET", "/", authenticated=False)
        self.assertEqual(code, 200)
        self.assertIn(b"Godot Doctor", body)
        self.assertIn(b'id="native-folder"', body)
        self.assertNotIn(b'id="browse-open"', body)
        self.assertIn(b'id="theme-switcher"', body)
        self.assertIn(b'id="fix-section"', body)
        self.assertIn(b'id="fix-apply"', body)
        self.assertIn(b'id="rule-help"', body)
        self.assertIn(b'id="rule-list"', body)
        self.assertIn(b'data-theme-choice="light"', body)
        self.assertIn(b'/static/theme.js', body)
        self.assertIn(b'rel="icon"', body)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
        for name, content_type in (
            ("styles.css", "text/css"),
            ("app.js", "text/javascript"),
            ("theme.js", "text/javascript"),
            ("favicon.svg", "image/svg+xml"),
        ):
            with self.subTest(asset=name):
                code, headers, asset = self.request(
                    "GET", f"/static/{name}", authenticated=False,
                )
                self.assertEqual(code, 200)
                self.assertTrue(headers["Content-Type"].startswith(content_type))
                self.assertTrue(asset)

    def test_light_palette_covers_every_dark_color_token(self) -> None:
        code, _, body = self.request("GET", "/static/styles.css", authenticated=False)
        self.assertEqual(code, 200)
        css = body.decode("utf-8")

        def block(selector: str) -> str:
            match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
            self.assertIsNotNone(match, selector)
            return match.group(1)

        def declarations(selector: str) -> dict[str, str]:
            return {
                name: value.strip()
                for name, value in re.findall(
                    r"--([\w-]+)\s*:\s*([^;]+);", block(selector),
                )
            }

        dark = declarations(":root")
        light = declarations(':root[data-theme="light"]')
        colors = {
            "canvas", "rail", "surface", "surface-raised", "surface-hover",
            "border", "border-strong", "text", "muted", "subtle", "accent",
            "accent-hover", "accent-ink", "error", "warning", "info",
            "shadow", "backdrop-alpha",
        }
        self.assertLessEqual(colors, set(dark))
        self.assertLessEqual(colors, set(light))
        self.assertIn("color-scheme: dark", block(":root"))
        self.assertIn("color-scheme: light", block(':root[data-theme="light"]'))
        for token in ("canvas", "surface", "text", "muted", "subtle", "accent"):
            with self.subTest(token=token):
                self.assertNotEqual(dark[token], light[token])


class GuiCliTests(unittest.TestCase):
    def test_gui_launch_does_not_require_an_existing_project(self) -> None:
        with patch("godot_doctor.gui.serve_gui", return_value=0) as serve:
            self.assertEqual(
                main([
                    "does-not-exist", "--gui", "--port", "0",
                    "--no-browser", "--exit-on-stdin-close",
                ]),
                0,
            )
            serve.assert_called_once_with(
                "does-not-exist", 0, False, exit_on_stdin_close=True,
            )

    def test_stdin_close_stops_the_gui_only_on_eof(self) -> None:
        stopped = threading.Event()
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "rb", 0)

        def close_write() -> None:
            try:
                os.close(write_fd)
            except OSError:
                pass

        self.addCleanup(stream.close)
        self.addCleanup(close_write)
        with patch("godot_doctor.gui.sys.stdin", SimpleNamespace(buffer=stream)):
            _watch_stdin(SimpleNamespace(shutdown=stopped.set))
            self.assertFalse(stopped.wait(0.2))
            close_write()
            self.assertTrue(stopped.wait(2))

    def test_gui_port_and_conflicting_flags_are_validated(self) -> None:
        for args in (
            ["--gui", "--port", "-1"], ["--gui", "--json"], ["--no-browser"],
            ["--exit-on-stdin-close"], [str(FIXTURES / "clean"), "--port", "0"],
        ):
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 2)

    def test_foreground_browser_does_not_block_server_startup(self) -> None:
        entered, release, served = threading.Event(), threading.Event(), threading.Event()
        server = MagicMock()
        server.origin = "http://127.0.0.1:8765"
        server.serve_forever.side_effect = lambda **kwargs: served.set()

        def blocking_browser(url):
            entered.set()
            release.wait(5)

        with patch("godot_doctor.gui.GuiServer", return_value=server), patch(
            "godot_doctor.gui.webbrowser.open", side_effect=blocking_browser
        ), redirect_stdout(io.StringIO()):
            worker = threading.Thread(target=serve_gui, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(served.wait(1), "server waited for the browser to exit")
            finally:
                release.set()
                worker.join(timeout=5)
        server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
