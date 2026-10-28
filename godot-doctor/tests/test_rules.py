from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godot_doctor.model import Severity
from godot_doctor.project import Project
from godot_doctor.rules import analyze

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class RuleTests(unittest.TestCase):
    def test_clean_fixture_is_silent(self) -> None:
        result = analyze(Project.load(FIXTURES / "clean"))
        self.assertEqual(result.diagnostics, [])
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 0)

    def test_broken_fixture_reports_every_expected_family(self) -> None:
        result = analyze(Project.load(FIXTURES / "broken"))
        rules = {diagnostic.rule for diagnostic in result.diagnostics}
        self.assertIn("broken-reference", rules)
        self.assertIn("case-mismatch", rules)
        self.assertIn("undefined-ext-resource", rules)
        self.assertIn("dependency-cycle", rules)

        messages = "\n".join(
            diagnostic.message for diagnostic in result.diagnostics
        )
        for needle in (
            "missing.tscn",
            "missing_autoload.gd",
            "missing_scene.tscn",
            "missing_resource.tres",
            "also_missing.tres",
            "Broken.gd",
            "a.tscn",
            "b.tscn",
        ):
            self.assertIn(needle, messages)

        self.assertGreaterEqual(result.error_count, 6)
        self.assertGreaterEqual(result.warning_count, 2)

    def test_prose_resource_paths_are_not_dependencies(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            scene = root / "main.tscn"
            scene.write_text(
                '[gd_scene format=3]\n[node name="Root" type="Label"]\n'
                'text = "See the guide at res://docs/readme.md for details."\n',
                encoding="utf-8",
            )
            self.assertEqual(analyze(Project.load(root)).diagnostics, [])
            scene.write_text(
                '[gd_scene format=3]\n[node name="Root" type="Node"]\n'
                'scene_file = "res://missing.tres"\n',
                encoding="utf-8",
            )
            result = analyze(Project.load(root))
            self.assertEqual(len(result.diagnostics), 1)
            self.assertEqual(result.diagnostics[0].rule, "broken-reference")

    def test_raw_string_resource_paths_are_checked(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            (root / "main.gd").write_text(
                'extends Node\n'
                'const MISSING = preload(r"res://missing_raw.tres")\n'
                '# preload(r"res://ignored_comment.tres")\n'
                'var text = "preload(r\\"res://ignored_string.tres\\")"\n',
                encoding="utf-8",
            )
            result = analyze(Project.load(root))
            self.assertEqual([diagnostic.rule for diagnostic in result.diagnostics], ["broken-reference"])
            self.assertIn("missing_raw.tres", result.diagnostics[0].message)

    def test_cycle_alone_is_a_warning_not_an_error(self) -> None:
        result = analyze(Project.load(FIXTURES / "warnings_only"))
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 1)
        self.assertEqual(result.diagnostics[0].rule, "dependency-cycle")

    def test_scene_transition_target_is_checked_without_an_eager_cycle(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            (root / "main.gd").write_text(
                'extends Node\nfunc go():\n'
                '\tget_tree().change_scene_to_file("res://next.tscn")\n',
                encoding="utf-8",
            )
            scene = root / "next.tscn"
            scene.write_text(
                '[gd_scene load_steps=2 format=3]\n'
                '[ext_resource type="Script" path="res://main.gd" id="1"]\n'
                '[node name="Root" type="Node"]\nscript = ExtResource("1")\n',
                encoding="utf-8",
            )
            self.assertEqual(analyze(Project.load(root)).diagnostics, [])
            scene.unlink()
            result = analyze(Project.load(root))
            self.assertEqual(len(result.diagnostics), 1)
            diagnostic = result.diagnostics[0]
            self.assertEqual(diagnostic.rule, "broken-reference")
            self.assertEqual(diagnostic.file, "main.gd")
            self.assertEqual(diagnostic.line, 3)
            self.assertIn("res://next.tscn", diagnostic.message)


class UIDRuleTests(unittest.TestCase):
    def analyze_files(self, files: dict[str, str]):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        for relative, text in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return analyze(Project.load(root))

    def test_duplicate_scene_and_resource_uid_is_an_error(self) -> None:
        header = '  [gd_scene format=3 uid="uid://bexample"]'
        result = self.analyze_files({
            "a.tscn": '\ufeff; scene header follows\n' + header + '\n[node name="Root" type="Node"]\n',
            "b.tres": '[gd_resource type="Resource" format=3 uid="uid://bexample"]\n[resource]\n',
        })
        self.assertEqual(len(result.diagnostics), 1)
        diagnostic = result.diagnostics[0]
        self.assertEqual(diagnostic.rule, "duplicate-uid")
        self.assertEqual(diagnostic.severity, Severity.ERROR)
        self.assertEqual(diagnostic.file, "a.tscn")
        self.assertEqual(diagnostic.line, 2)
        self.assertEqual(diagnostic.column, header.index("uid://") + 1)
        self.assertEqual(diagnostic.related, ("b.tres",))
        self.assertIn("uid://bexample", diagnostic.message)
        self.assertIn("b.tres", diagnostic.message)
        self.assertEqual(result.error_count, 1)

    def test_shared_ext_resource_uids_are_references_not_declarations(self) -> None:
        result = self.analyze_files({
            "a.tscn": '[gd_scene format=3 uid="uid://bscene"]\n'
                '[ext_resource type="Resource" uid="uid://basset" path="res://asset.tres" id="1"]\n'
                '[node name="Root" type="Node"]\n',
            "b.tscn": '[gd_scene format=3 uid="uid://cscene"]\n'
                '[ext_resource type="Resource" uid="uid://basset" path="res://asset.tres" id="1"]\n'
                '[node name="Root" type="Node"]\n',
            "asset.tres": '[gd_resource type="Resource" format=3 uid="uid://basset"]\n[resource]\n',
        })
        self.assertEqual(result.diagnostics, [])

    def test_duplicate_script_and_shader_sidecars(self) -> None:
        for extension, body in (
            ("gd", "extends Node\n"),
            ("gdshader", "shader_type spatial;\n"),
            ("gdshaderinc", "const float VALUE = 1.0;\n"),
        ):
            with self.subTest(extension=extension):
                first, second = f"a.{extension}", f"b.{extension}"
                result = self.analyze_files({
                    first: body,
                    second: body,
                    first + ".uid": "\ufeffuid://bexample\n",
                    second + ".uid": "uid://bexample\n",
                })
                self.assertEqual(len(result.diagnostics), 1)
                diagnostic = result.diagnostics[0]
                self.assertEqual(diagnostic.rule, "duplicate-uid")
                self.assertEqual(diagnostic.file, first + ".uid")
                self.assertEqual((diagnostic.line, diagnostic.column), (1, 1))
                self.assertEqual(diagnostic.related, (second + ".uid",))

    def test_import_metadata_participates_without_reading_generated_paths(self) -> None:
        result = self.analyze_files({
            "a.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>\n',
            "a.svg.import": '[remap]\nimporter="texture"\nuid="uid://bexample"\n'
                'path="res://.godot/imported/a.svg-deadbeef.ctex"\n'
                '[deps]\nsource_file="res://a.svg"\n'
                'dest_files=["res://.godot/imported/a.svg-deadbeef.ctex"]\n',
            "b.tres": '[gd_resource type="Resource" format=3 uid="uid://bexample"]\n[resource]\n',
            "c.gd": "extends Node\n",
            "c.gd.uid": "uid://bexample\n",
        })
        self.assertEqual(len(result.diagnostics), 1)
        diagnostic = result.diagnostics[0]
        self.assertEqual(diagnostic.rule, "duplicate-uid")
        self.assertEqual(diagnostic.file, "a.svg.import")
        self.assertEqual((diagnostic.line, diagnostic.column), (3, 6))
        self.assertEqual(diagnostic.related, ("b.tres", "c.gd.uid"))
        self.assertEqual(result.files_analyzed, 4)

    def test_distinct_declarations_across_all_formats_are_silent(self) -> None:
        result = self.analyze_files({
            "a.tscn": '[gd_scene format=3 uid="uid://bscene"]\n[node name="Root" type="Node"]\n',
            "b.gd": "extends Node\n",
            "b.gd.uid": "uid://bscript\n",
            "c.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>\n',
            "c.svg.import": '[remap]\nuid="uid://btexture"\n'
                'path="res://.godot/imported/c.ctex"\n[params]\nuid="uid://bscene"\n',
        })
        self.assertEqual(result.diagnostics, [])

    def test_caches_and_unowned_sidecars_do_not_declare_uids(self) -> None:
        result = self.analyze_files({
            "a.tscn": '[gd_scene format=3 uid="uid://bexample"]\n[node name="Root" type="Node"]\n',
            "a.tscn.uid": "uid://bexample\n",
            "deleted.gd.uid": "uid://bexample\n",
            "deleted.svg.import": '[remap]\nuid="uid://bexample"\n',
            ".godot/cached.tres": '[gd_resource uid="uid://bexample"]\n',
            ".import/cached.tres": '[gd_resource uid="uid://bexample"]\n',
        })
        self.assertEqual(result.diagnostics, [])
        self.assertNotIn(".godot/cached.tres", result.project.files)
        self.assertNotIn(".import/cached.tres", result.project.files)

    def test_missing_empty_and_invalid_uids_do_not_collide(self) -> None:
        for uid in (None, "", "uid://<invalid>", "uid://BAD", "uid://bad_value"):
            with self.subTest(uid=uid):
                attribute = "" if uid is None else f' uid="{uid}"'
                header = f'[gd_scene format=3{attribute}]\n[node name="Root" type="Node"]\n'
                result = self.analyze_files({"a.tscn": header, "b.tscn": header})
                self.assertEqual(result.diagnostics, [])

    def test_different_uid_text_can_declare_the_same_numeric_id(self) -> None:
        for first, second in (
            ("uid://b", "uid://ab"),
            ("uid://z", "uid://0"),
            ("uid://9", "uid://ba"),
            ("uid://", "uid://a"),
            ("uid://b", "uid://d4n4ub6itg402"),
        ):
            with self.subTest(first=first, second=second):
                result = self.analyze_files({
                    "a.gd": "extends Node\n",
                    "a.gd.uid": first + "\n",
                    "b.gd": "extends Node\n",
                    "b.gd.uid": second + "\n",
                })
                self.assertEqual(len(result.diagnostics), 1)
                self.assertEqual(result.diagnostics[0].rule, "duplicate-uid")
                self.assertEqual(result.diagnostics[0].related, ("b.gd.uid",))

    def test_scene_property_text_cannot_replace_the_header_uid(self) -> None:
        for header, expected_count in (
            ('[gd_scene format=3 uid="uid://bexample"]', 1),
            ('[gd_scene format=3 uid="uid://cexample"]', 0),
            ('[gd_scene format=3]', 0),
        ):
            with self.subTest(header=header):
                result = self.analyze_files({
                    "a.tscn": header + '\n[node name="Root" type="Label"]\n'
                        'text = "Example:\n[gd_scene format=3]\n'
                        '[gd_scene format=3 uid=uid://bexample]\n"\n',
                    "b.tscn": '[gd_scene format=3 uid="uid://bexample"]\n'
                        '[node name="Root" type="Node"]\n',
                })
                self.assertEqual(len(result.diagnostics), expected_count)
                if expected_count:
                    diagnostic = result.diagnostics[0]
                    self.assertEqual(diagnostic.rule, "duplicate-uid")
                    self.assertEqual(diagnostic.line, 1)
                    self.assertEqual(diagnostic.related, ("b.tscn",))

    def test_import_property_text_cannot_replace_the_remap_uid(self) -> None:
        for uid, expected_count in (("bexample", 1), ("cexample", 0)):
            with self.subTest(uid=uid):
                result = self.analyze_files({
                    "a.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>\n',
                    "a.svg.import": '[remap]\nuid="uid://' + uid + '"\n'
                        '[params]\nlabel = "Example:\n[remap]\n'
                        'uid=uid://bexample\n"\n',
                    "b.tres": '[gd_resource type="Resource" format=3 uid="uid://bexample"]\n'
                        '[resource]\n',
                })
                self.assertEqual(len(result.diagnostics), expected_count)
                if expected_count:
                    diagnostic = result.diagnostics[0]
                    self.assertEqual(diagnostic.rule, "duplicate-uid")
                    self.assertEqual((diagnostic.line, diagnostic.column), (2, 6))

    def test_uid_column_points_to_the_attribute_not_an_earlier_equal_string(self) -> None:
        header = '[gd_resource type="Resource" note="uid://bexample" format=3 uid="uid://bexample"]'
        result = self.analyze_files({
            "a.tres": header + '\n[resource]\n',
            "b.tres": '[gd_resource type="Resource" format=3 uid="uid://bexample"]\n[resource]\n',
        })
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].column, header.rindex("uid://") + 1)

    def test_uid_scan_is_deterministic_read_only_and_refreshes_after_repair(self) -> None:
        result = self.analyze_files({
            "b.gd": "extends Node\n",
            "b.gd.uid": "uid://bexample\n",
            "a.gd": "extends Node\n",
            "a.gd.uid": "uid://bexample\n",
        })
        self.assertEqual(len(result.diagnostics), 1)
        root = result.project.root
        before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        self.assertEqual(analyze(Project.load(root)).diagnostics, result.diagnostics)
        after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        (root / "b.gd.uid").write_text("uid://cexample\n", encoding="utf-8")
        self.assertEqual(analyze(Project.load(root)).diagnostics, [])


if __name__ == "__main__":
    unittest.main()
