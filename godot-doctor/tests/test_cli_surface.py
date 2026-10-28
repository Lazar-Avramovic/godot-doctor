from __future__ import annotations

import io
import json
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from godot_doctor import __version__
from godot_doctor.cli import COMMANDS, main
from godot_doctor.gui_service import GuiService
from godot_doctor.inspect import FILE_KINDS, REFERENCE_STATUSES
from godot_doctor.project import Project
from godot_doctor.rulebook import RULEBOOK
from godot_doctor.rules import analyze

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


def invoke(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, stdout.getvalue(), stderr.getvalue()


def write_tree(root: Path, files: dict[str, bytes | str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


class RulebookTests(unittest.TestCase):
    def test_every_emitted_rule_is_documented(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "coverage"
            write_tree(
                root,
                {
                    "project.godot": (
                        "config_version=5\n"
                        "oops\n"
                        "# folded into the next key\n"
                        "[application]\n"
                        'config/name="Coverage"\n'
                        'run/main_scene="res://missing_main.tscn"\n'
                    ),
                    "a.tscn": (
                        '[gd_scene load_steps=5 format=3]\n'
                        '\n'
                        '[ext_resource type="PackedScene" path="res://b.tscn" id="1"]\n'
                        '[ext_resource type="PackedScene" path="res://b.tscn" id="1"]\n'
                        '[ext_resource type="Script" path="C:/outside.gd" id="2"]\n'
                        '[ext_resource type="Script"]\n'
                        '\n'
                        '[node name="Root" type="Node"]\n'
                        '\n'
                        '[node name="X" parent="." instance=ExtResource("ghost")]\n'
                        'prop = "res://../escape.tres"\n'
                        'folder = "res://sub"\n'
                        'missing = "res://missing.tres"\n'
                        'wrong_case = "res://Case.gd"\n'
                        'sub = SubResource("ghost_sub")\n'
                    ),
                    "b.tscn": (
                        '[gd_scene load_steps=2 format=3]\n'
                        '[ext_resource type="PackedScene" path="res://a.tscn" id="1"]\n'
                        '[node name="B" type="Node"]\n'
                    ),
                    "case.gd": "extends Node\n",
                    "sub/keep.txt": "placeholder\n",
                    "u1.gd": "extends Node\n",
                    "u1.gd.uid": "uid://bexample\n",
                    "u2.gd": "extends Node\n",
                    "u2.gd.uid": "uid://bexample\n",
                },
            )
            result = analyze(Project.load(root))
            emitted = {item.rule for item in result.diagnostics}
            for item in result.diagnostics:
                self.assertEqual(
                    item.severity.value, RULEBOOK[item.rule].severity, item.rule
                )
        expected = {
            "broken-reference",
            "case-mismatch",
            "invalid-resource-path",
            "resource-is-directory",
            "dependency-cycle",
            "duplicate-uid",
            "parse-error",
            "duplicate-ext-resource",
            "invalid-ext-resource",
            "undefined-ext-resource",
            "undefined-sub-resource",
            "config-hash-comment",
            "config-nonkey-line",
        }
        self.assertEqual(emitted, expected)
        self.assertTrue(emitted <= set(RULEBOOK))
        self.assertEqual(len(RULEBOOK), 15)
        for rule in RULEBOOK.values():
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.title)
                self.assertIn(rule.severity, ("error", "warning", "info"))
                self.assertTrue(rule.description)
                self.assertTrue(rule.remediation)

    def test_rules_and_explain_json(self) -> None:
        code, stdout, stderr = invoke(["rules", "--json"])
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["count"], 15)
        self.assertEqual({rule["id"] for rule in payload["rules"]}, set(RULEBOOK))

        code, stdout, stderr = invoke(["explain", "broken-reference", "--json"])
        self.assertEqual((code, stderr), (0, ""))
        rule = json.loads(stdout)["rule"]
        self.assertEqual(rule["id"], "broken-reference")
        self.assertTrue(rule["remediation"])

    def test_unknown_rule_is_an_error_with_a_suggestion(self) -> None:
        code, stdout, stderr = invoke(["explain", "broken-refrence"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("did you mean 'broken-reference'", stderr)

        code, stdout, stderr = invoke(["explain", "broken-refrence", "--json"])
        self.assertEqual((code, stdout), (2, ""))
        error = json.loads(stderr)
        self.assertEqual(error["error"]["code"], "unknown-rule")
        self.assertIn("did you mean", error["error"]["message"])


class FingerprintTests(unittest.TestCase):
    def project(self, root: Path) -> None:
        write_tree(
            root,
            {
                "project.godot": "config_version=5\n",
                "main.tscn": (
                    '[gd_scene load_steps=2 format=3]\n'
                    '[ext_resource type="PackedScene" path="res://missing.tscn" id="1"]\n'
                    '[node name="Root" type="Node"]\n'
                ),
            },
        )

    def fingerprints(self, root: Path) -> list[str]:
        code, stdout, stderr = invoke(["scan", str(root), "--json"])
        self.assertEqual(code, 1, stderr)
        return [row["fingerprint"] for row in json.loads(stdout)["diagnostics"]]

    def test_fingerprint_ignores_line_drift_but_tracks_target_changes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            before = self.fingerprints(root)
            self.assertEqual(len(before), 1)

            scene = (root / "main.tscn").read_text(encoding="utf-8")
            (root / "main.tscn").write_text("\n" + scene, encoding="utf-8")
            self.assertEqual(self.fingerprints(root), before)

            (root / "main.tscn").write_text(
                scene.replace("missing.tscn", "other.tscn"), encoding="utf-8"
            )
            self.assertNotEqual(self.fingerprints(root), before)


class ScanFormatTests(unittest.TestCase):
    def test_json_filter_counts_and_exit_follow_the_filtered_view(self) -> None:
        code, stdout, _ = invoke(["scan", str(FIXTURES / "broken"), "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["tool"]["version"], __version__)
        self.assertEqual(payload["summary"]["hidden"], 0)
        self.assertEqual(payload["summary"]["total"], len(payload["diagnostics"]))

        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--severity", "warning", "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(
            {row["severity"] for row in payload["diagnostics"]}, {"warning"}
        )
        self.assertEqual(payload["summary"]["errors"], 0)
        self.assertEqual(payload["summary"]["hidden"], 6)
        self.assertEqual(payload["summary"]["total"], 8)

    def test_rule_and_ignore_filters(self) -> None:
        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--rule", "dependency-cycle", "--json"]
        )
        self.assertEqual(code, 0)
        rows = json.loads(stdout)["diagnostics"]
        self.assertEqual([row["rule"] for row in rows], ["dependency-cycle"])

        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--ignore-rule", "broken-reference", "--json"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertNotIn("broken-reference", {row["rule"] for row in payload["diagnostics"]})
        self.assertEqual(payload["summary"]["hidden"], 5)

    def test_search_matches_message_file_or_rule_case_insensitively(self) -> None:
        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--search", "MISSING_SCENE", "--json"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertEqual(len(payload["diagnostics"]), 1)
        self.assertEqual(payload["diagnostics"][0]["rule"], "broken-reference")
        self.assertEqual(payload["summary"]["hidden"], 7)

        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--search", "broken.gd", "--json"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(stdout)["diagnostics"]), 3)

        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--search", "dependency-cycle", "--quiet"]
        )
        self.assertEqual(code, 0)

    def test_file_glob_filter(self) -> None:
        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--file", "*.gd", "--json"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertEqual({row["file"] for row in payload["diagnostics"]}, {"broken.gd"})
        self.assertEqual(payload["summary"]["hidden"], 6)

    def test_unknown_rule_and_severity_are_usage_errors(self) -> None:
        for args in (
            ["scan", str(FIXTURES / "clean"), "--rule", "nope"],
            ["scan", str(FIXTURES / "clean"), "--severity", "fatal"],
            ["scan", str(FIXTURES / "clean"), "--json", "--format", "sarif"],
        ):
            with self.subTest(args=args):
                code, stdout, stderr = invoke(args)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("error:", stderr)

    def test_ndjson_shape(self) -> None:
        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--format", "ndjson"]
        )
        self.assertEqual(code, 1)
        rows = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual(rows[0]["type"], "meta")
        self.assertEqual(rows[0]["schema_version"], 2)
        self.assertEqual(rows[-1]["type"], "summary")
        self.assertEqual(len(rows), 8 + 2)
        self.assertEqual(
            {row["type"] for row in rows[1:-1]}, {"diagnostic"}
        )

    def test_sarif_is_well_formed_and_clean_is_empty(self) -> None:
        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--format", "sarif"]
        )
        self.assertEqual(code, 1)
        sarif = json.loads(stdout)
        self.assertEqual(sarif["version"], "2.1.0")
        run = sarif["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "godot-doctor")
        self.assertEqual(run["columnKind"], "unicodeCodePoints")
        self.assertTrue(run["originalUriBaseIds"]["%SRCROOT%"]["uri"].endswith("/"))
        self.assertEqual(len(run["results"]), 8)
        self.assertEqual(
            {result["level"] for result in run["results"]}, {"error", "warning"}
        )
        self.assertTrue(all("godotDoctor/v1" in result["partialFingerprints"] for result in run["results"]))
        self.assertTrue(
            any(rule["id"] == "broken-reference" for rule in run["tool"]["driver"]["rules"])
        )

        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "clean"), "--format", "sarif", "--fail-on", "never"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["runs"][0]["results"], [])

    def test_sarif_encodes_paths_with_spaces(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_tree(
                root,
                {
                    "project.godot": "config_version=5\n",
                    "with space.tscn": (
                        '[gd_scene format=3]\n'
                        '[ext_resource type="PackedScene" path="res://missing one.tscn" id="1"]\n'
                    ),
                },
            )
            code, stdout, stderr = invoke(["scan", str(root), "--format", "sarif"])
            self.assertEqual(code, 1, stderr)
            location = json.loads(stdout)["runs"][0]["results"][0]["locations"][0]
            self.assertEqual(
                location["physicalLocation"]["artifactLocation"]["uri"],
                "with%20space.tscn",
            )

    def test_github_annotations(self) -> None:
        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--format", "github"]
        )
        self.assertEqual(code, 1)
        lines = stdout.splitlines()
        self.assertTrue(any(line.startswith("::error ") for line in lines))
        self.assertTrue(any(line.startswith("::warning ") for line in lines))
        self.assertIn("title=broken-reference", stdout)
        self.assertTrue(lines[-1].startswith("::notice title=godot-doctor::"))

    def test_output_file_quiet_and_determinism(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            code, stdout, stderr = invoke(
                ["scan", str(FIXTURES / "broken"), "--json", "--output", str(target)]
            )
            self.assertEqual((code, stdout, stderr), (1, "", ""))
            first = target.read_text(encoding="utf-8")
            self.assertEqual(json.loads(first)["summary"]["errors"], 6)

            code, stdout, stderr = invoke(
                ["scan", str(FIXTURES / "broken"), "--json", "--output", str(target)]
            )
            self.assertEqual((code, stdout, stderr), (1, "", ""))
            self.assertEqual(target.read_text(encoding="utf-8"), first)

        code, stdout, stderr = invoke(["scan", str(FIXTURES / "broken"), "--quiet"])
        self.assertEqual((code, stdout, stderr), (1, "", ""))

    def test_gui_exports_are_the_same_bytes_as_cli_output(self) -> None:
        service = GuiService()
        state = service.start(str(FIXTURES / "broken"))
        service._thread.join(timeout=5)
        self.assertEqual(state["status"], "running")
        snapshot = service.snapshot()
        self.assertEqual(snapshot["status"], "complete")
        gui_json, _, _ = service.export("json", snapshot["run_id"])
        gui_text, _, _ = service.export("text", snapshot["run_id"])

        code, stdout, stderr = invoke(["scan", str(FIXTURES / "broken"), "--json"])
        self.assertEqual((code, stderr), (1, ""))
        self.assertEqual(stdout.rstrip("\n"), gui_json)

        code, stdout, stderr = invoke(["scan", str(FIXTURES / "broken")])
        self.assertEqual((code, stderr), (1, ""))
        self.assertEqual(stdout.rstrip("\n"), gui_text)

    def test_source_and_browse_match_gui_service(self) -> None:
        service = GuiService()
        state = service.start(str(FIXTURES / "broken"))
        service._thread.join(timeout=5)
        run_id = service.snapshot()["run_id"]
        gui_source = service.source("broken.gd", 3, run_id)
        code, stdout, stderr = invoke(
            ["context", "broken.gd:3", "--path", str(FIXTURES / "broken"), "--json"]
        )
        self.assertEqual((code, stderr), (0, ""))
        cli_source = json.loads(stdout)
        for key in ("file", "line", "lines", "has_more_before", "has_more_after"):
            self.assertEqual(cli_source[key], gui_source[key], key)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_tree(
                root,
                {
                    "first/project.godot": "config_version=5\n",
                    "plain/readme.txt": "hello\n",
                    "file.txt": "not a directory\n",
                },
            )
            gui_browse = service.browse(str(root))
            code, stdout, stderr = invoke(["browse", str(root), "--json"])
            self.assertEqual((code, stderr), (0, ""))
            cli_browse = json.loads(stdout)
            for key in (
                "path", "parent", "is_project", "directories", "unreadable_count",
            ):
                self.assertEqual(cli_browse[key], gui_browse[key], key)

    def test_structured_and_plain_project_errors(self) -> None:
        code, stdout, stderr = invoke(["scan", "does-not-exist", "--json"])
        self.assertEqual((code, stdout), (2, ""))
        error = json.loads(stderr)
        self.assertEqual(error["error"]["code"], "project-error")
        self.assertIn("does not exist", error["error"]["message"])

        code, stdout, stderr = invoke(["scan", "does-not-exist"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertTrue(stderr.startswith("godot-doctor: "))
        self.assertNotIn('"error"', stderr)

    def test_unexpected_errors_exit_three(self) -> None:
        with patch("godot_doctor.cli.analyze", side_effect=RuntimeError("boom")):
            code, stdout, stderr = invoke(["scan", str(FIXTURES / "clean")])
        self.assertEqual(code, 3)
        self.assertEqual(stdout, "")
        self.assertIn("RuntimeError: boom", stderr)

    def test_grouped_text_keeps_root_file_names(self) -> None:
        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--group-by", "directory"]
        )
        self.assertEqual(code, 1)
        self.assertIn("\n  project.godot:6:17:", stdout)
        self.assertNotIn("\n  oject.godot:", stdout)
        self.assertIn("broken-reference", stdout)

        code, stdout, _ = invoke(
            ["scan", str(FIXTURES / "broken"), "--group-by", "rule"]
        )
        self.assertEqual(code, 1)
        self.assertIn("broken-reference (5)", stdout)

    def test_group_by_is_text_only(self) -> None:
        code, _, stderr = invoke(
            ["scan", str(FIXTURES / "clean"), "--json", "--group-by", "rule"]
        )
        self.assertEqual(code, 2)
        self.assertIn("--group-by", stderr)

    def test_scan_accepts_a_file_inside_the_project(self) -> None:
        code, stdout, stderr = invoke(
            ["scan", str(FIXTURES / "broken" / "broken.gd"), "--json"]
        )
        self.assertEqual(code, 1, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["project"], str((FIXTURES / "broken").resolve()))
        self.assertEqual(payload["summary"]["errors"], 6)

    def test_unicode_paths_round_trip_through_ascii_json(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "project-ž"
            write_tree(
                root,
                {
                    "project.godot": "config_version=5\n",
                    "scéna.tscn": (
                        '[gd_scene format=3]\n'
                        '[ext_resource type="PackedScene" path="res://chybí.tscn" id="1"]\n'
                    ),
                },
            )
            code, stdout, stderr = invoke(["scan", str(root), "--json"])
            self.assertEqual(code, 1, stderr)
            self.assertTrue(stdout.isascii())
            payload = json.loads(stdout)
            self.assertEqual(payload["diagnostics"][0]["file"], "scéna.tscn")
            self.assertIn("chybí.tscn", payload["diagnostics"][0]["message"])

    def test_fail_on_thresholds(self) -> None:
        cases = (
            ([], 0),
            (["--fail-on", "never"], 0),
            (["--fail-on", "warning"], 1),
            (["--fail-on", "error"], 0),
            (["--strict"], 1),
        )
        for flags, expected in cases:
            with self.subTest(flags=flags):
                code, _, _ = invoke(["scan", str(FIXTURES / "warnings_only"), *flags])
                self.assertEqual(code, expected)


class ContextTests(unittest.TestCase):
    def test_excerpt_text_and_json(self) -> None:
        code, stdout, stderr = invoke(
            ["context", "broken.gd:3", "--path", str(FIXTURES / "broken")]
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("broken.gd:3", stdout)
        self.assertIn("3 | const Missing = preload", stdout)

        code, stdout, _ = invoke(
            [
                "context",
                str(FIXTURES / "broken" / "broken.gd"),
                "--line",
                "3",
                "--radius",
                "1",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["project"], str((FIXTURES / "broken").resolve()))
        self.assertEqual(payload["file"], "broken.gd")
        self.assertEqual(payload["line"], 3)
        self.assertEqual([row["number"] for row in payload["lines"]], [2, 3, 4])
        self.assertTrue(payload["has_more_before"])
        self.assertTrue(payload["has_more_after"])

    def test_outside_root_and_out_of_range_are_refused(self) -> None:
        code, stdout, stderr = invoke(
            [
                "context",
                "../clean/main.gd",
                "--path",
                str(FIXTURES / "broken"),
            ]
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("outside the selected project", stderr)

        code, _, stderr = invoke(
            ["context", "broken.gd", "--line", "999", "--path", str(FIXTURES / "broken")]
        )
        self.assertEqual(code, 2)
        self.assertIn("does not exist", stderr)

    def test_binary_and_unknown_files_are_refused(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_tree(
                root,
                {
                    "project.godot": "config_version=5\n",
                    "blob.txt": b"before\x00after",
                },
            )
            code, _, stderr = invoke(["context", "blob.txt", "--path", str(root)])
            self.assertEqual(code, 2)
            self.assertIn("binary", stderr)

            code, _, stderr = invoke(["context", "missing.gd", "--path", str(root)])
            self.assertEqual(code, 2)
            self.assertIn("cannot read", stderr)


class DiscoverBrowseTests(unittest.TestCase):
    def tree(self, root: Path) -> None:
        write_tree(
            root,
            {
                "first/project.godot": 'config_version=5\n[application]\nconfig/name="First"\n',
                "nested/deep/second/project.godot": "config_version=4\n",
                ".godot/generated/project.godot": "config_version=5\n",
                "node_modules/dependency/project.godot": "config_version=5\n",
                ".hidden/secret/project.godot": "config_version=5\n",
                "plain/readme.txt": "not a project\n",
            },
        )

    def test_discover_skips_generated_and_hidden_directories(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.tree(root)
            code, stdout, stderr = invoke(["discover", str(root), "--json"])
            self.assertEqual((code, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual(payload["count"], 2)
            self.assertEqual(
                [row["relative"] for row in payload["projects"]],
                ["first", "nested/deep/second"],
            )
            self.assertEqual(payload["projects"][0]["name"], "First")
            self.assertEqual(payload["projects"][1]["config_version"], "4")
            self.assertFalse(payload["truncated"])

            code, stdout, _ = invoke(["discover", str(root), "--depth", "1", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual([row["relative"] for row in json.loads(stdout)["projects"]], ["first"])

            code, stdout, _ = invoke(["discover", str(root), "--limit", "1", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["count"], 1)
            self.assertTrue(payload["truncated"])

    def test_discover_missing_path_is_an_error(self) -> None:
        code, _, stderr = invoke(["discover", "does-not-exist"])
        self.assertEqual(code, 2)
        self.assertIn("path does not exist", stderr)

    def test_browse_lists_directories_and_marks_projects(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project" / "project.godot").parent.mkdir(parents=True)
            (root / "project" / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            (root / "plain").mkdir()
            (root / "file.txt").write_text("not a directory", encoding="utf-8")

            code, stdout, stderr = invoke(["browse", str(root), "--json"])
            self.assertEqual((code, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual([row["name"] for row in payload["directories"]], ["plain", "project"])
            self.assertTrue(payload["directories"][1]["is_project"])
            self.assertFalse(payload["directories"][0]["is_project"])
            self.assertFalse(payload["is_project"])

            code, stdout, _ = invoke(["browse", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("* ", stdout)
            self.assertIn("project", stdout)

            code, _, stderr = invoke(["browse", str(root / "missing")])
            self.assertEqual(code, 2)
            self.assertIn("cannot open", stderr)


class ReferencesGraphStatsTests(unittest.TestCase):
    def test_refs_filters_and_kinds(self) -> None:
        code, stdout, stderr = invoke(["refs", str(FIXTURES / "broken"), "--json"])
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["total"], len(payload["references"]))
        self.assertEqual(payload["summary"]["unresolved"], 6)
        self.assertEqual(
            set(payload["summary"]["by_status"]), set(REFERENCE_STATUSES)
        )
        self.assertEqual(
            {row["status"] for row in payload["references"]},
            {"ok", "missing", "case-mismatch"},
        )
        missing = next(row for row in payload["references"] if row["status"] == "missing")
        self.assertTrue(missing["target"])
        lazy = next(row for row in payload["references"] if row["kind"] == "load")
        self.assertFalse(lazy["eager"])
        eager = next(row for row in payload["references"] if row["kind"] == "preload")
        self.assertTrue(eager["eager"])
        mismatch = next(
            row for row in payload["references"] if row["status"] == "case-mismatch"
        )
        self.assertEqual(mismatch["actual"], "broken.gd")

        code, stdout, _ = invoke(
            ["refs", str(FIXTURES / "broken"), "--unresolved", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertTrue(
            all(row["status"] != "ok" for row in json.loads(stdout)["references"])
        )

        code, stdout, _ = invoke(
            ["refs", str(FIXTURES / "broken"), "--kind", "preload", "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["references"][0]["kind"], "preload")

        code, stdout, stderr = invoke(
            ["refs", str(FIXTURES / "broken"), "--kind", "bogus"]
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("unknown reference kind", stderr)

    def test_graph_text_json_and_dot(self) -> None:
        code, stdout, stderr = invoke(["graph", str(FIXTURES / "broken"), "--format", "json"])
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["edges"], 2)
        self.assertEqual(payload["summary"]["cycles"], 1)
        self.assertIn("a.tscn", payload["cycles"][0]["nodes"])
        self.assertIn("b.tscn", payload["cycles"][0]["nodes"])

        code, stdout, _ = invoke(["graph", str(FIXTURES / "broken")])
        self.assertEqual(code, 0)
        self.assertIn("cycle: a.tscn -> b.tscn -> a.tscn", stdout)

        code, stdout, _ = invoke(["graph", str(FIXTURES / "broken"), "--format", "dot"])
        self.assertEqual(code, 0)
        self.assertTrue(stdout.startswith("digraph godot_project {"))
        self.assertIn('"a.tscn" -> "b.tscn";', stdout)

        code, stdout, _ = invoke(["graph", str(FIXTURES / "broken"), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["summary"]["cycles"], 1)

        code, stdout, _ = invoke(["graph", str(FIXTURES / "clean"), "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["summary"]["cycles"], 0)

    def test_stats_correlate_with_the_analysis(self) -> None:
        code, stdout, stderr = invoke(["stats", str(FIXTURES / "broken"), "--json"])
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["files"]["indexed"], 6)
        self.assertEqual(payload["diagnostics"]["total"], 8)
        self.assertEqual(sum(payload["diagnostics"]["by_rule"].values()), 8)
        self.assertEqual(payload["diagnostics"]["errors"], 6)
        self.assertEqual(payload["references"]["unresolved"], 6)
        self.assertEqual(
            set(payload["diagnostics"]["by_rule"]), set(RULEBOOK)
        )
        self.assertEqual(
            set(payload["references"]["by_status"]), set(REFERENCE_STATUSES)
        )
        self.assertEqual(set(payload["files"]["by_kind"]), set(FILE_KINDS))
        self.assertEqual(
            set(payload["uids"]),
            {"declared", "invalid", "distinct", "duplicate_groups"},
        )
        self.assertTrue(payload["diagnostics"]["top_files"])

        code, stdout, _ = invoke(["stats", str(FIXTURES / "broken")])
        self.assertEqual(code, 0)
        self.assertIn("Files: 6 indexed", stdout)
        self.assertIn("Diagnostics: 6 error(s)", stdout)

        code, stdout, _ = invoke(["stats", str(FIXTURES / "clean"), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["diagnostics"]["total"], 0)

        code, _, stderr = invoke(["stats", str(FIXTURES / "clean"), "--top", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--top", stderr)


class DiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.root = Path(self._directory.name) / "project"
        write_tree(
            self.root,
            {
                "project.godot": "config_version=5\n",
                "main.tscn": (
                    '[gd_scene load_steps=2 format=3]\n'
                    '[ext_resource type="PackedScene" path="res://missing.tscn" id="1"]\n'
                    '[node name="Root" type="Node"]\n'
                ),
            },
        )

    def tearDown(self) -> None:
        self._directory.cleanup()

    def baseline(self) -> Path:
        target = Path(self._directory.name) / "baseline.json"
        code, stdout, stderr = invoke(["scan", str(self.root), "--json"])
        self.assertEqual(code, 1, stderr)
        target.write_text(stdout, encoding="utf-8")
        return target

    def test_same_project_persists_and_repair_resolves(self) -> None:
        baseline = self.baseline()
        code, stdout, stderr = invoke(
            ["diff", str(baseline), str(self.root), "--json"]
        )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["new"]["total"], 0)
        self.assertEqual(payload["summary"]["resolved"]["total"], 0)
        self.assertEqual(payload["summary"]["persisting"], 1)

        write_tree(
            self.root,
            {"missing.tscn": '[gd_scene format=3]\n[node name="M" type="Node"]\n'},
        )
        code, stdout, _ = invoke(["diff", str(baseline), str(self.root), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["resolved"]["total"], 1)
        self.assertEqual(payload["summary"]["new"]["total"], 0)
        self.assertEqual(payload["resolved"][0]["rule"], "broken-reference")

    def test_new_error_fails_and_line_drift_does_not(self) -> None:
        baseline = self.baseline()
        scene = (self.root / "main.tscn").read_text(encoding="utf-8")
        (self.root / "main.tscn").write_text("\n" + scene, encoding="utf-8")
        code, stdout, _ = invoke(["diff", str(baseline), str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["summary"]["new"]["total"], 0)

        (self.root / "main.tscn").write_text(
            scene
            + '[ext_resource type="PackedScene" path="res://second_missing.tscn" id="2"]\n',
            encoding="utf-8",
        )
        code, stdout, _ = invoke(["diff", str(baseline), str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("second_missing.tscn", stdout)
        self.assertIn("1 new", stdout)

    def test_legacy_baseline_without_fingerprints_or_schema(self) -> None:
        baseline = self.baseline()
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        payload.pop("schema_version")
        payload.pop("tool")
        for row in payload["diagnostics"]:
            row.pop("fingerprint")
        baseline.write_text(json.dumps(payload), encoding="utf-8")
        code, stdout, _ = invoke(["diff", str(baseline), str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["new"], [])

    def test_baseline_matches_across_a_reworded_message(self) -> None:
        baseline = self.baseline()
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertTrue(payload["diagnostics"][0].get("anchor"))
        payload["diagnostics"][0]["message"] = "a different wording of the same defect"
        baseline.write_text(json.dumps(payload), encoding="utf-8")
        code, stdout, stderr = invoke(["diff", str(baseline), str(self.root), "--json"])
        self.assertEqual((code, stderr), (0, ""))
        summary = json.loads(stdout)["summary"]
        self.assertEqual((summary["new"]["total"], summary["resolved"]["total"]), (0, 0))
        self.assertEqual(summary["persisting"], 1)

    def test_legacy_baseline_matches_a_reworded_message_at_the_same_line(self) -> None:
        legacy = Path(self._directory.name) / "legacy.json"
        legacy.write_text(
            json.dumps(
                {
                    "project": str(self.root),
                    "diagnostics": [
                        {
                            "rule": "broken-reference",
                            "severity": "error",
                            "message": "an older wording of this defect",
                            "file": "main.tscn",
                            "line": 2,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        code, stdout, stderr = invoke(["diff", str(legacy), str(self.root), "--json"])
        self.assertEqual((code, stderr), (0, ""))
        summary = json.loads(stdout)["summary"]
        self.assertEqual((summary["new"]["total"], summary["resolved"]["total"]), (0, 0))
        self.assertEqual(summary["persisting"], 1)

    def test_legacy_config_rule_name_matches_after_the_split(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_tree(root, {"project.godot": "config_version=5\noops\n"})
            legacy = Path(temporary) / "legacy.json"
            legacy.write_text(
                json.dumps(
                    {
                        "project": str(root),
                        "diagnostics": [
                            {
                                "rule": "config-parse-error",
                                "severity": "warning",
                                "message": "expected a key=value line",
                                "file": "project.godot",
                                "line": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = invoke(["diff", str(legacy), str(root), "--json"])
            self.assertEqual((code, stderr), (0, ""))
            summary = json.loads(stdout)["summary"]
            self.assertEqual(
                (summary["new"]["total"], summary["resolved"]["total"]), (0, 0)
            )
            self.assertEqual(summary["persisting"], 1)

    def test_strict_warning_and_fail_on_never(self) -> None:
        baseline = Path(self._directory.name) / "empty.json"
        code, stdout, _ = invoke(["scan", str(FIXTURES / "clean"), "--json"])
        self.assertEqual(code, 0)
        baseline.write_text(stdout, encoding="utf-8")

        code, _, _ = invoke(
            ["diff", str(baseline), str(FIXTURES / "warnings_only")]
        )
        self.assertEqual(code, 0)
        code, _, _ = invoke(
            ["diff", str(baseline), str(FIXTURES / "warnings_only"), "--strict"]
        )
        self.assertEqual(code, 1)

        bad = self.baseline()
        write_tree(
            self.root,
            {"missing2.tscn": "", "second.tscn": '[gd_scene format=3]\n[ext_resource type="PackedScene" path="res://nope.tscn" id="1"]\n'},
        )
        code, _, _ = invoke(
            ["diff", str(bad), str(self.root), "--fail-on", "never"]
        )
        self.assertEqual(code, 0)

    def test_filters_apply_to_current_and_baseline(self) -> None:
        baseline = self.baseline()
        code, stdout, stderr = invoke(
            [
                "diff",
                str(baseline),
                str(self.root),
                "--ignore-rule",
                "broken-reference",
                "--json",
            ]
        )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["new"]["total"], 0)
        self.assertEqual(payload["summary"]["resolved"]["total"], 0)
        self.assertEqual(payload["summary"]["persisting"], 0)
        self.assertEqual(payload["summary"]["hidden"], 1)
        self.assertEqual(payload["summary"]["baseline_hidden"], 1)

    def test_bad_baseline_is_a_structured_error(self) -> None:
        missing = Path(self._directory.name) / "missing.json"
        code, stdout, stderr = invoke(["diff", str(missing), str(self.root), "--json"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(json.loads(stderr)["error"]["code"], "baseline-error")

        bad = Path(self._directory.name) / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        code, _, stderr = invoke(["diff", str(bad), str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", stderr)

    def test_non_utf8_baseline_is_a_structured_error(self) -> None:
        for label, payload in (
            ("utf-16", json.dumps({"diagnostics": []}).encode("utf-16")),
            ("latin-1", b'{"diagnostics": [], "note": "caf\xe9"}'),
        ):
            with self.subTest(encoding=label):
                path = Path(self._directory.name) / f"{label}.json"
                path.write_bytes(payload)
                code, stdout, stderr = invoke(["diff", str(path), str(self.root), "--json"])
                self.assertEqual((code, stdout), (2, ""))
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(json.loads(stderr)["error"]["code"], "baseline-error")


class DescribeTests(unittest.TestCase):
    def test_describe_covers_every_command(self) -> None:
        code, stdout, stderr = invoke(["describe"])
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["tool"]["version"], __version__)
        self.assertEqual([row["name"] for row in payload["commands"]], list(COMMANDS))
        by_name = {row["name"]: row for row in payload["commands"]}
        flags = {
            flag for option in by_name["scan"]["options"] for flag in option["flags"]
        }
        for expected in (
            "--rule", "--severity", "--search", "--format", "--fail-on", "--output",
        ):
            self.assertIn(expected, flags)
        self.assertEqual(
            set(payload["formats"]), {"text", "json", "ndjson", "sarif", "github"}
        )
        self.assertEqual(set(payload["exit_codes"]), {"0", "1", "2", "3"})
        self.assertIn("project-error", payload["error_codes"])
        self.assertIn("baseline-error", payload["error_codes"])
        required = set(payload["report_schema"]["required"])
        self.assertEqual(
            required, {"schema_version", "tool", "project", "summary", "diagnostics"}
        )
        self.assertIn("fingerprint", payload["report_schema"]["properties"]["diagnostics"]["items"]["required"])

    def test_declared_json_keys_are_present_in_real_output(self) -> None:
        code, stdout, _ = invoke(["describe"])
        self.assertEqual(code, 0)
        declared = {row["name"]: row["json_keys"] for row in json.loads(stdout)["commands"]}

        with TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "baseline.json"
            code, stdout, _ = invoke(["scan", str(FIXTURES / "broken"), "--json"])
            self.assertEqual(code, 1)
            baseline.write_text(stdout, encoding="utf-8")
            cases = {
                "scan": ["scan", str(FIXTURES / "broken"), "--json"],
                "fix": ["fix", str(FIXTURES / "broken"), "--json"],
                "rules": ["rules", "--json"],
                "explain": ["explain", "broken-reference", "--json"],
                "context": [
                    "context",
                    str(FIXTURES / "broken" / "broken.gd"),
                    "--json",
                ],
                "refs": ["refs", str(FIXTURES / "broken"), "--json"],
                "graph": ["graph", str(FIXTURES / "broken"), "--format", "json"],
                "stats": ["stats", str(FIXTURES / "broken"), "--json"],
                "discover": ["discover", str(FIXTURES), "--json"],
                "browse": ["browse", str(FIXTURES), "--json"],
                "diff": ["diff", str(baseline), str(FIXTURES / "broken"), "--json"],
                "describe": ["describe"],
            }
            self.assertEqual(set(cases), set(declared))
            for name, argv in cases.items():
                with self.subTest(command=name):
                    code, stdout, stderr = invoke(argv)
                    self.assertIn(code, (0, 1), stderr)
                    payload = json.loads(stdout)
                    for key in declared[name]:
                        self.assertIn(key, payload)

    def test_version_matches_pyproject(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            metadata = tomllib.load(handle)
        self.assertEqual(metadata["project"]["version"], __version__)

    def test_help_command(self) -> None:
        for argv in (["help"], ["help", "scan"], ["--help"]):
            with self.subTest(argv=argv):
                code, stdout, stderr = invoke(argv)
                self.assertEqual((code, stderr), (0, ""))
                self.assertIn("usage:", stdout)
        code, stdout, stderr = invoke(["help", "nonsense"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("unknown command", stderr)


if __name__ == "__main__":
    unittest.main()
