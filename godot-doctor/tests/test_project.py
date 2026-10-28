from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from godot_doctor.model import Severity
from godot_doctor.project import (
    Project,
    ProjectError,
    extract_res_paths,
    find_project_root,
)
from godot_doctor.rules import analyze

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ProjectTests(unittest.TestCase):
    def test_finds_root_from_nested_directory(self) -> None:
        root = find_project_root(FIXTURES / "clean" / "data")
        self.assertEqual(root, (FIXTURES / "clean").resolve())

    def test_project_indexes_files_and_config(self) -> None:
        project = Project.load(FIXTURES / "clean")
        self.assertEqual(project.main_scene, "res://main.tscn")
        self.assertEqual(
            project.autoloads["GameState"], "res://autoload/game_state.gd"
        )
        self.assertIn("main.tscn", project.files)
        self.assertIn("data/player_stats.tres", project.files)
        self.assertNotIn(".godot/ignored.txt", project.files)

    def test_source_sidecars_are_indexed_but_cache_directories_are_not(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            for relative in ("icon.svg.import", "main.gd.uid", ".godot/cache.import", ".import/cache.uid"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            project = Project.load(root)
            self.assertEqual(set(project.files), {"project.godot", "icon.svg.import", "main.gd.uid"})

    def test_missing_project_raises(self) -> None:
        with self.assertRaises(ProjectError):
            Project.load(FIXTURES / "does-not-exist")

    def test_resolution(self) -> None:
        project = Project.load(FIXTURES / "clean")
        self.assertEqual(
            project.resolve("res://data/player_stats.tres"),
            "data/player_stats.tres",
        )
        self.assertIsNone(project.resolve("res://../outside.tscn"))
        self.assertEqual(project.resolve("res://"), "")

    def test_extract_res_paths_handles_star_autoload(self) -> None:
        self.assertEqual(
            extract_res_paths('"*res://autoload/game_state.gd"'),
            ["res://autoload/game_state.gd"],
        )

    def test_extract_res_paths_ignores_prose_but_keeps_whole_values(self) -> None:
        self.assertEqual(
            extract_res_paths('text = "Read the guide at res://docs/intro.md first."'),
            [],
        )
        self.assertEqual(
            extract_res_paths('texture = "res://icon.svg"'),
            ["res://icon.svg"],
        )
        self.assertEqual(
            extract_res_paths('paths = ["res://a.tres", "res://b.tres"]'),
            ["res://a.tres", "res://b.tres"],
        )

    def test_unreadable_directories_are_reported_not_silently_skipped(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            self.assertEqual(analyze(Project.load(root)).diagnostics, [])
            blocked = root / "private"

            def walk(path, onerror=None, **kwargs):
                if onerror is not None:
                    onerror(PermissionError(13, "Permission denied", str(blocked)))
                yield str(path), ["private"], []

            with patch("godot_doctor.project.os.walk", side_effect=walk):
                result = analyze(Project.load(root))
            self.assertEqual(
                [diagnostic.rule for diagnostic in result.diagnostics],
                ["unreadable-directory"],
            )
            diagnostic = result.diagnostics[0]
            self.assertEqual(diagnostic.severity, Severity.WARNING)
            self.assertEqual(diagnostic.file, "private")
            self.assertIn("Permission denied", diagnostic.message)


if __name__ == "__main__":
    unittest.main()
