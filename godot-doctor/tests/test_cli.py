from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from godot_doctor.cli import _configure_console, main

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class CliTests(unittest.TestCase):
    def test_clean_exit_zero(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main([str(FIXTURES / "clean")])
        self.assertEqual(code, 0)
        self.assertIn("No issues found.", stdout.getvalue())

    def test_broken_exit_one(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main([str(FIXTURES / "broken")])
        self.assertEqual(code, 1)
        output = stdout.getvalue()
        self.assertIn("broken-reference", output)
        self.assertIn("dependency-cycle", output)

    def test_json_output(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main([str(FIXTURES / "broken"), "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertGreater(payload["summary"]["errors"], 0)
        self.assertTrue(
            any(
                diagnostic["rule"] == "dependency-cycle"
                for diagnostic in payload["diagnostics"]
            )
        )

    def test_strict_promotes_warning_to_failure(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = main([str(FIXTURES / "warnings_only")])
        self.assertEqual(code, 0)

        with redirect_stdout(io.StringIO()):
            strict_code = main([str(FIXTURES / "warnings_only"), "--strict"])
        self.assertEqual(strict_code, 1)

    def test_console_errors_do_not_crash_on_undecodable_names(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict", newline="\n")
        try:
            with self.assertRaises(UnicodeEncodeError):
                stream.write("bad\udcff")
            with patch("sys.stdout", stream), patch("sys.stderr", stream):
                _configure_console()
                print("bad\udcff")
                stream.flush()
            self.assertIn(r"bad\udcff", raw.getvalue().decode("ascii"))
        finally:
            stream.detach()

    def test_missing_project_exit_two(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main([str(FIXTURES / "does-not-exist")])
        self.assertEqual(code, 2)
        self.assertIn("path does not exist", stderr.getvalue())

    def test_module_reports_uid_collisions_and_passes_after_repair(self) -> None:
        source = str(Path(__file__).resolve().parents[1] / "src")
        environment = {**os.environ, "PYTHONPATH": source}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            header = '[gd_scene format=3 uid="uid://bexample"]\n[node name="Root" type="Node"]\n'
            for name in ("a.tscn", "b.tscn"):
                (root / name).write_text(header, encoding="utf-8")
            for flags in ([], ["--json"], ["--strict"], ["--json", "--strict"]):
                with self.subTest(flags=flags):
                    process = subprocess.run(
                        [sys.executable, "-m", "godot_doctor", str(root), *flags],
                        cwd=root, env=environment, text=True, encoding="utf-8",
                        capture_output=True, timeout=30,
                    )
                    self.assertEqual(process.returncode, 1, process.stderr)
                    self.assertEqual(process.stderr, "")
                    if "--json" in flags:
                        payload = json.loads(process.stdout)
                        self.assertEqual(payload["summary"]["errors"], 1)
                        self.assertEqual(payload["summary"]["warnings"], 0)
                        self.assertEqual(len(payload["diagnostics"]), 1)
                        diagnostic = payload["diagnostics"][0]
                        self.assertEqual(diagnostic["rule"], "duplicate-uid")
                        self.assertEqual(diagnostic["file"], "a.tscn")
                        self.assertEqual(diagnostic["related"], ["b.tscn"])
                    else:
                        self.assertIn("a.tscn:1:", process.stdout)
                        self.assertIn("[duplicate-uid]", process.stdout)
                        self.assertIn("b.tscn", process.stdout)
            (root / "b.tscn").write_text(header.replace("bexample", "cexample"), encoding="utf-8")
            process = subprocess.run(
                [sys.executable, "-m", "godot_doctor", str(root), "--json", "--strict"],
                cwd=root, env=environment, text=True, encoding="utf-8",
                capture_output=True, timeout=30,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)["diagnostics"], [])


if __name__ == "__main__":
    unittest.main()
