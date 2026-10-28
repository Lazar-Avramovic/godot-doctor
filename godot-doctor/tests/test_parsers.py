from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godot_doctor.parsers import parse_gdscript_file, parse_scene_file, parse_uid_file
from godot_doctor.project import Project

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_scene_ext_resources_and_edges(self) -> None:
        project = Project.load(FIXTURES / "clean")
        parsed = parse_scene_file(project, "main.tscn")
        self.assertEqual(parsed.kind, "scene")
        self.assertEqual(parsed.ext_resources["1_main"].path, "res://main.gd")
        self.assertEqual(
            parsed.ext_resources["2_player"].type, "PackedScene"
        )
        self.assertIn("res://player.tscn", parsed.eager_edges)
        self.assertEqual(parsed.diagnostics, [])

    def test_gdscript_extracts_preload_and_load(self) -> None:
        project = Project.load(FIXTURES / "clean")
        parsed = parse_gdscript_file(project, "main.gd")
        refs = {(ref.raw, ref.kind, ref.eager) for ref in parsed.refs}
        self.assertIn(("res://player.tscn", "preload", True), refs)
        self.assertIn(("res://data/player_stats.tres", "load", False), refs)
        self.assertEqual(parsed.eager_edges, {"res://player.tscn"})

    def test_gdscript_comments_and_strings_are_not_references(self) -> None:
        project = Project.load(FIXTURES / "clean")
        parsed = parse_gdscript_file(project, "scripts/comment_traps.gd")
        self.assertEqual(parsed.refs, [])
        self.assertEqual(parsed.eager_edges, set())

    def test_literal_scene_transitions_are_lazy_references(self) -> None:
        source = (
            'extends Node\nfunc go():\n'
            '\tget_tree().change_scene_to_file("res://next.tscn")\n'
            '\tself.get_tree().change_scene_to_file(\n'
            '\t\t# a multiline call\n\t\t"res://other.tscn"\n\t)\n'
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "main.gd"
            path.write_text(source, encoding="utf-8")
            project = Project(root=root, config_path=root / "project.godot", files={path.name: path})
            parsed = parse_gdscript_file(project, path.name)
            self.assertEqual(
                [(ref.raw, ref.kind, ref.eager, ref.line) for ref in parsed.refs],
                [("res://next.tscn", "change_scene_to_file", False, 3),
                 ("res://other.tscn", "change_scene_to_file", False, 4)],
            )
            self.assertEqual(parsed.refs[0].column, source.splitlines()[2].index("change_scene_to_file") + 1)
            self.assertEqual(parsed.eager_edges, set())
            self.assertEqual(parsed.diagnostics, [])

    def test_scene_transition_comments_strings_and_dynamic_paths_are_ignored(self) -> None:
        source = (
            'extends Node\n'
            '# get_tree().change_scene_to_file("res://comment.tscn")\n'
            'var text = \'get_tree().change_scene_to_file("res://string.tscn")\'\n'
            'var doc = """get_tree().change_scene_to_file("res://doc.tscn")"""\n'
            'func go(path):\n'
            '\tget_tree().change_scene_to_file(path)\n'
            '\tget_tree().change_scene_to_file("res://" + path)\n'
            '\tother.change_scene_to_file("res://unrelated.tscn")\n'
            '\tfake_get_tree().change_scene_to_file("res://unrelated.tscn")\n'
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "main.gd"
            path.write_text(source, encoding="utf-8")
            project = Project(root=root, config_path=root / "project.godot", files={path.name: path})
            parsed = parse_gdscript_file(project, path.name)
            self.assertEqual(parsed.refs, [])
            self.assertEqual(parsed.eager_edges, set())

    def test_undefined_ext_resource_is_reported(self) -> None:
        project = Project.load(FIXTURES / "broken")
        parsed = parse_scene_file(project, "broken.tscn")
        rules = {diagnostic.rule for diagnostic in parsed.diagnostics}
        self.assertIn("undefined-ext-resource", rules)

    def parse_scene(self, text: str):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        (root / "a.tscn").write_text(text, encoding="utf-8")
        return parse_scene_file(Project.load(root), "a.tscn")

    def test_scene_prose_does_not_create_ext_or_sub_references(self) -> None:
        prose = self.parse_scene(
            '[gd_scene format=3]\n[node name="Root" type="Label"]\n'
            'text = "Mentions ExtResource(\\"9\\") and SubResource(\\"8\\") as text."\n'
        )
        self.assertEqual(prose.diagnostics, [])
        self.assertEqual(prose.refs, [])
        control = self.parse_scene(
            '[gd_scene format=3]\n[node name="Root" type="Node"]\n'
            'note = ExtResource("9")\nsub = SubResource("8")\n'
        )
        self.assertEqual(
            [diagnostic.rule for diagnostic in control.diagnostics],
            ["undefined-ext-resource", "undefined-sub-resource"],
        )
        lookalike = self.parse_scene(
            '[gd_scene format=3]\n[node name="Root" type="Node"]\n'
            'note = NotExtResource("9")\nsub = NotSubResource("8")\n'
        )
        self.assertEqual(lookalike.diagnostics, [])

    def test_scene_multiline_text_cannot_define_resources(self) -> None:
        parsed = self.parse_scene(
            '[gd_scene format=3]\n[node name="Root" type="Label"]\n'
            'text = "First line\n'
            '[ext_resource type="Script" path="res://missing.gd" id="9"]\n'
            'See res://missing.tres"\n'
        )
        self.assertEqual(parsed.ext_resources, {})
        self.assertEqual(parsed.refs, [])
        self.assertEqual(parsed.diagnostics, [])

    def test_uid_sidecar_reads_only_first_line_and_handles_bom(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "main.gd.uid"
            path.write_text("\ufeff  uid://bexample\nuid://cexample\n", encoding="utf-8")
            project = Project(root=root, config_path=root / "project.godot", files={path.name: path})
            parsed = parse_uid_file(project, path.name)
            self.assertEqual(parsed.uid, "uid://bexample")
            self.assertEqual((parsed.uid_line, parsed.uid_column), (1, 3))
            self.assertEqual(parsed.refs, [])
            self.assertEqual(parsed.diagnostics, [])

    def test_import_parser_only_reads_remap_uid(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "icon.svg.import"
            path.write_text(
                '\ufeff; uid="uid://comment"\n[remap]\n'
                '  uid = "uid://bexample" ; uid="uid://comment"\n'
                'path="res://.godot/imported/icon.ctex"\n'
                '[params]\nuid="uid://parameter"\n', encoding="utf-8",
            )
            project = Project(root=root, config_path=root / "project.godot", files={path.name: path})
            parsed = parse_uid_file(project, path.name)
            self.assertEqual(parsed.uid, "uid://bexample")
            self.assertEqual((parsed.uid_line, parsed.uid_column), (3, 10))
            self.assertEqual(parsed.refs, [])
            self.assertEqual(parsed.eager_edges, set())
            self.assertEqual(parsed.diagnostics, [])

    def test_import_parser_resumes_after_multiline_strings_and_escaped_quotes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "icon.svg.import"
            path.write_text(
                '[params]\nlabel="Example: \\"\n'
                '[remap]\nuid=uid://cexample\n"\n'
                '; unmatched quote in comment: "\n'
                '[remap] ; another comment: "\n'
                'uid="uid://bexample"\n', encoding="utf-8",
            )
            project = Project(root=root, config_path=root / "project.godot", files={path.name: path})
            parsed = parse_uid_file(project, path.name)
            self.assertEqual(parsed.uid, "uid://bexample")
            self.assertEqual((parsed.uid_line, parsed.uid_column), (8, 6))
            self.assertEqual(parsed.diagnostics, [])

    def test_unreadable_metadata_reports_parse_error(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("main.gd.uid", "icon.svg.import"):
                with self.subTest(name=name):
                    path = root / name
                    path.write_bytes(b"\xff")
                    project = Project(root=root, config_path=root / "project.godot", files={name: path})
                    parsed = parse_uid_file(project, name)
                    self.assertIsNone(parsed.uid)
                    self.assertEqual(len(parsed.diagnostics), 1)
                    self.assertEqual(parsed.diagnostics[0].rule, "parse-error")
                    self.assertEqual(parsed.diagnostics[0].file, name)

    def test_metadata_without_a_uid_is_silent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, text in (("main.gd.uid", ""), ("icon.svg.import", '[remap]\nimporter="texture"\n')):
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(text, encoding="utf-8")
                    project = Project(root=root, config_path=root / "project.godot", files={name: path})
                    parsed = parse_uid_file(project, name)
                    self.assertIsNone(parsed.uid)
                    self.assertEqual(parsed.diagnostics, [])


if __name__ == "__main__":
    unittest.main()
