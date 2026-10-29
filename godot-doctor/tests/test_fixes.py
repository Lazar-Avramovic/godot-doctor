from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from godot_doctor.cli import main
from godot_doctor.fixes import (
    FixChange,
    FixError,
    FixPlan,
    apply_fixes,
    plan_config_fixes,
)
from godot_doctor.project import Project
from godot_doctor.rules import analyze

HASH_CONFIG = (
    "config_version=5\n"
    "\n"
    "[application]\n"
    "\n"
    "# The version below is written by the build.\n"
    'config/version="1.0"\n'
)


def invoke(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, stdout.getvalue(), stderr.getvalue()


def project_with(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text(text, encoding="utf-8")
    return root


class FixPlanTests(unittest.TestCase):
    def test_plans_hash_lines_and_ignores_a_clean_config(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            plan = plan_config_fixes(Project.load(root))
            self.assertEqual(len(plan.changes), 1)
            change = plan.changes[0]
            self.assertEqual(change.file, "project.godot")
            self.assertEqual(change.line, 5)
            self.assertEqual(change.before, "# The version below is written by the build.")
            self.assertEqual(change.after, "; The version below is written by the build.")
            self.assertEqual(plan.skipped, ())
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), "config_version=5\n")
            plan = plan_config_fixes(Project.load(root))
            self.assertEqual((plan.changes, plan.skipped), ((), ()))

    def test_preview_does_not_write(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            before = (root / "project.godot").read_bytes()
            plan_config_fixes(Project.load(root))
            self.assertEqual((root / "project.godot").read_bytes(), before)

    def test_apply_preserves_bom_crlf_indentation_and_non_ascii(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = (
                b"\xef\xbb\xbfconfig_version=5\r\n"
                b"\r\n"
                b"[application]\r\n"
                b"\t# verze \xc5\xbe\r\n"
                b'config/version="1.0"\r\n'
            )
            (root / "project.godot").write_bytes(raw)
            plan = plan_config_fixes(Project.load(root))
            self.assertEqual(len(plan.changes), 1)
            applied = apply_fixes(plan)
            self.assertEqual(len(applied.changes), 1)
            self.assertEqual(
                (root / "project.godot").read_bytes(),
                raw.replace(b"\t# verze", b"\t; verze"),
            )
            self.assertEqual(analyze(Project.load(root)).diagnostics, [])

    def test_hash_line_containing_equals_is_still_flagged_and_fixed(self) -> None:
        text = "config_version=5\n# note = this is not a setting\n"
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), text)
            result = analyze(Project.load(root))
            self.assertEqual(
                [diagnostic.rule for diagnostic in result.diagnostics],
                ["config-hash-comment"],
            )
            self.assertEqual(result.diagnostics[0].severity.value, "error")
            plan = plan_config_fixes(Project.load(root))
            self.assertEqual(plan.changes[0].after, "; note = this is not a setting")

    def test_non_hash_parse_error_is_not_a_fixer_candidate(self) -> None:
        text = "config_version=5\noops\n"
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), text)
            plan = plan_config_fixes(Project.load(root))
            self.assertEqual(plan.changes, ())
            self.assertEqual(plan.skipped, ())
            diagnostic = analyze(Project.load(root)).diagnostics[0]
            self.assertEqual(diagnostic.rule, "config-nonkey-line")
            self.assertEqual(diagnostic.severity.value, "info")

    def test_apply_refuses_a_line_that_changed_after_the_preview(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            plan = plan_config_fixes(Project.load(root))
            changed = HASH_CONFIG.replace("# The version", "# A newer note")
            (root / "project.godot").write_text(changed, encoding="utf-8")
            with self.assertRaises(FixError) as error:
                apply_fixes(plan)
            self.assertIn("changed since the preview", str(error.exception))
            self.assertEqual(
                (root / "project.godot").read_text(encoding="utf-8"), changed
            )

    def test_apply_is_idempotent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            plan = plan_config_fixes(Project.load(root))
            apply_fixes(plan)
            second = plan_config_fixes(Project.load(root))
            self.assertEqual(second.changes, ())
            self.assertEqual(apply_fixes(second).changes, ())
            self.assertIn("; The version below", (root / "project.godot").read_text())

    def test_apply_refuses_a_path_outside_the_project(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), "config_version=5\n")
            plan = FixPlan(
                project=Project.load(root),
                changes=(FixChange("../outside.godot", 1, "# x", "; x"),),
            )
            with self.assertRaises(FixError) as error:
                apply_fixes(plan)
            self.assertIn("outside the project", str(error.exception))


class FixCommandTests(unittest.TestCase):
    def test_dry_run_previews_without_writing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            code, stdout, stderr = invoke(["fix", str(root)])
            self.assertEqual((code, stderr), (0, ""))
            self.assertIn("Automatic repair ready: 1 line in project.godot", stdout)
            self.assertIn("Preview only.", stdout)
            self.assertIn("--apply", stdout)
            self.assertIn("# The version below", (root / "project.godot").read_text())

    def test_apply_repairs_and_reports_the_verified_remainder(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            self.assertEqual(invoke(["scan", str(root)])[0], 1)
            code, stdout, stderr = invoke(["fix", str(root), "--apply"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertIn("Applied 1 automatic fix", stdout)
            self.assertIn("Remaining after rescan: 0 error(s)", stdout)
            self.assertIn("; The version below", (root / "project.godot").read_text())
            self.assertEqual(invoke(["scan", str(root)])[0], 0)

    def test_json_shape_for_preview_apply_and_nothing_to_fix(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            code, stdout, stderr = invoke(["fix", str(root), "--json"])
            self.assertEqual((code, stderr), (0, ""))
            preview = json.loads(stdout)
            self.assertEqual(preview["applied"], False)
            self.assertIsNone(preview["remaining"])
            self.assertEqual(
                preview["summary"], {"files": 1, "lines": 1, "skipped": 0}
            )
            self.assertEqual(
                preview["files"][0]["changes"][0]["line"], 5
            )
            code, stdout, stderr = invoke(["fix", str(root), "--apply", "--json"])
            self.assertEqual((code, stderr), (0, ""))
            applied = json.loads(stdout)
            self.assertEqual(applied["applied"], True)
            self.assertEqual(applied["remaining"]["total"], 0)
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), "config_version=5\n")
            code, stdout, stderr = invoke(["fix", str(root), "--json"])
            self.assertEqual((code, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual(payload["summary"], {"files": 0, "lines": 0, "skipped": 0})
            self.assertEqual(payload["files"], [])
            self.assertEqual(payload["skipped"], [])

    def test_clean_project_says_there_is_nothing_to_fix(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), "config_version=5\n")
            code, stdout, stderr = invoke(["fix", str(root)])
            self.assertEqual((code, stderr), (0, ""))
            self.assertIn("No automatic fixes available", stdout)

    def test_format_json_matches_the_json_flag(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            code, stdout, stderr = invoke(["fix", str(root), "--format", "json"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["summary"]["lines"], 1)
            code, _, _ = invoke(["fix", str(root), "--json"])
            self.assertEqual(code, 0)
            code, stdout, _ = invoke(
                ["fix", str(root), "--json", "--format", "text"]
            )
            self.assertEqual((code, stdout), (2, ""))

    def test_missing_project_is_a_usage_error(self) -> None:
        code, stdout, stderr = invoke(["fix", str(Path("does-not-exist"))])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("path does not exist", stderr)


class ConfigSeverityTests(unittest.TestCase):
    def test_hash_line_reports_the_silent_loss_consequence_as_an_error(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), HASH_CONFIG)
            code, stdout, stderr = invoke(["scan", str(root), "--json"])
            self.assertEqual(code, 1, stderr)
            diagnostic = json.loads(stdout)["diagnostics"][0]
            self.assertEqual(diagnostic["rule"], "config-hash-comment")
            self.assertEqual(diagnostic["severity"], "error")
            self.assertIn("silently lose a setting", diagnostic["message"])

    def test_plain_non_key_line_is_information_and_does_not_fail(self) -> None:
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), "config_version=5\noops\n")
            code, stdout, stderr = invoke(["scan", str(root), "--json"])
            self.assertEqual((code, stderr), (0, ""))
            diagnostic = json.loads(stdout)["diagnostics"][0]
            self.assertEqual(diagnostic["rule"], "config-nonkey-line")
            self.assertEqual(diagnostic["severity"], "info")
            self.assertEqual(diagnostic["message"], "expected a key=value line")
            code, _, _ = invoke(["scan", str(root), "--fail-on", "info"])
            self.assertEqual(code, 1)


class ContinuationTests(unittest.TestCase):
    def test_multiline_input_values_are_not_config_errors(self) -> None:
        text = (
            "config_version=5\n"
            "\n"
            "[input]\n"
            "\n"
            "move_forward={\n"
            '"deadzone": 0.5,\n'
            '"events": [Object(InputEventKey,"resource_local_to_scene":false,'
            '"resource_name":"","device":-1,"keycode":87,"pressed":false)\n'
            ", Object(InputEventJoypadMotion,\"axis\":1,\"axis_value\":1.0)\n"
            "]\n"
            "}\n"
            "\n"
            "[autoload]\n"
            'Game="*res://missing_autoload.gd"\n'
        )
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), text)
            result = analyze(Project.load(root))
            self.assertEqual(
                [diagnostic.rule for diagnostic in result.diagnostics],
                ["broken-reference"],
            )
            self.assertEqual(result.diagnostics[0].file, "project.godot")
            self.assertEqual(result.diagnostics[0].line, 13)

    def test_res_paths_on_continuation_lines_are_references(self) -> None:
        text = (
            "config_version=5\n"
            "[application]\n"
            "paths={\n"
            '"a": "res://missing_a.tres",\n'
            '"b": "res://missing_b.tres"\n'
            "}\n"
        )
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), text)
            result = analyze(Project.load(root))
            self.assertEqual(
                [(row.rule, row.line) for row in result.diagnostics],
                [("broken-reference", 4), ("broken-reference", 5)],
            )

    def test_unclosed_value_is_an_error_at_the_opening_line(self) -> None:
        text = (
            "config_version=5\n"
            "[application]\n"
            "data={\n"
            '"a": 1\n'
        )
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), text)
            result = analyze(Project.load(root))
            self.assertEqual(
                [diagnostic.rule for diagnostic in result.diagnostics],
                ["config-unclosed-value"],
            )
            diagnostic = result.diagnostics[0]
            self.assertEqual(diagnostic.severity.value, "error")
            self.assertEqual(diagnostic.line, 3)
            code, stdout, stderr = invoke(["scan", str(root), "--json"])
            self.assertEqual(code, 1, stderr)
            self.assertEqual(
                json.loads(stdout)["diagnostics"][0]["rule"],
                "config-unclosed-value",
            )
            plan = plan_config_fixes(Project.load(root))
            self.assertEqual((plan.changes, plan.skipped), ((), ()))

    def test_closed_multiline_value_has_no_unclosed_diagnostic(self) -> None:
        text = "config_version=5\n[application]\ndata={\n\"a\": 1\n}\n"
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), text)
            self.assertEqual(analyze(Project.load(root)).diagnostics, [])

    def test_hash_text_inside_a_multiline_value_is_not_a_key(self) -> None:
        text = (
            "config_version=5\n"
            "[application]\n"
            "data={\n"
            "# value text, not a key\n"
            '"x": 1\n'
            "}\n"
        )
        with TemporaryDirectory() as temporary:
            root = project_with(Path(temporary), text)
            self.assertEqual(analyze(Project.load(root)).diagnostics, [])


class FailedWriteHygieneTests(unittest.TestCase):
    def test_failed_apply_leaves_no_temporary_file_behind(self) -> None:
        """The temp inherits the target's mode, so a read-only target left an
        undeletable temp in the project whenever the rename failed on Windows."""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "project.godot"
            config.write_text(HASH_CONFIG, encoding="utf-8")
            os.chmod(config, 0o444)
            plan = plan_config_fixes(Project.load(root))
            self.assertTrue(plan.changes)

            def denied(*_args, **_kwargs):
                raise OSError(13, "denied")

            original, os.replace = os.replace, denied
            try:
                with self.assertRaises(FixError):
                    apply_fixes(plan)
            finally:
                os.replace = original
                os.chmod(config, 0o666)

            self.assertEqual(list(root.glob(".project.godot*")), [])
            self.assertEqual(config.read_text(encoding="utf-8"), HASH_CONFIG)


if __name__ == "__main__":
    unittest.main()