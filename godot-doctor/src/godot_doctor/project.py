from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import Diagnostic, Reference, Severity

IGNORED_DIRS = {
    ".godot",
    ".git",
    ".import",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
}
IGNORED_SUFFIXES = (".pyc",)

RES_ANY_RE = re.compile(r"res://[^\s\"'<>(),;\[\]]+")
QUOTED_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')


class ProjectError(Exception):
    """Raised when a Godot project cannot be located or read."""


def strip_comment(line: str) -> str:
    """Remove a semicolon comment while preserving semicolons inside strings."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == ";":
            return line[:index]
    return line


def extract_res_paths(value: str) -> list[str]:
    """Extract res:// paths from a Godot config value or property string."""
    paths: list[str] = []
    quoted_spans: list[tuple[int, int]] = []
    for match in QUOTED_RE.finditer(value):
        quoted_spans.append((match.start(), match.end()))
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        if inner is None:
            continue
        if inner.startswith("*"):
            inner = inner[1:]
        # Only a whole quoted value is a path; prose that mentions res:// is text.
        if inner.startswith("res://") and inner not in paths:
            paths.append(inner)
    for match in RES_ANY_RE.finditer(value):
        if any(start <= match.start() < end for start, end in quoted_spans):
            continue
        raw = match.group(0)
        if raw not in paths:
            paths.append(raw)
    return paths


def find_project_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        raise ProjectError(f"cannot resolve path {candidate}: {exc}") from exc
    if not candidate.exists():
        raise ProjectError(f"path does not exist: {candidate}")
    start = candidate if candidate.is_dir() else candidate.parent
    for directory in (start, *start.parents):
        if (directory / "project.godot").is_file():
            return directory
    raise ProjectError(f"no project.godot found in {start} or any parent directory")


@dataclass
class Project:
    root: Path
    config_path: Path
    files: dict[str, Path] = field(default_factory=dict)
    casefold: dict[str, list[str]] = field(default_factory=dict)
    main_scene: str | None = None
    autoloads: dict[str, str] = field(default_factory=dict)
    refs: list[Reference] = field(default_factory=list)
    scan_diagnostics: list[Diagnostic] = field(default_factory=list)
    parse_diagnostics: list[Diagnostic] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        root = find_project_root(path)
        project = cls(root=root, config_path=root / "project.godot")
        project.scan_files()
        project.parse_config()
        return project

    def scan_files(self) -> None:
        def record_unreadable(error: OSError) -> None:
            filename = error.filename
            try:
                relative = Path(filename).relative_to(self.root).as_posix() if filename else "."
            except (TypeError, ValueError):
                relative = "."
            self.scan_diagnostics.append(
                Diagnostic(
                    rule="unreadable-directory",
                    severity=Severity.WARNING,
                    message=f"cannot read this directory: {error.strerror or error}",
                    file=relative or ".",
                )
            )

        for dirpath, dirnames, filenames in os.walk(self.root, onerror=record_unreadable):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in IGNORED_DIRS and not name.startswith(".tmp")
            )
            for filename in sorted(filenames):
                if filename.endswith(IGNORED_SUFFIXES):
                    continue
                absolute = Path(dirpath) / filename
                try:
                    relative = absolute.relative_to(self.root)
                except ValueError:
                    continue
                rel = relative.as_posix()
                self.files[rel] = absolute
                self.casefold.setdefault(rel.casefold(), []).append(rel)

    def parse_config(self) -> None:
        try:
            text = self.config_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise ProjectError(f"cannot read {self.config_path}: {exc}") from exc

        section = ""
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            line = strip_comment(raw_line).strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if "=" not in line:
                self.parse_diagnostics.append(
                    Diagnostic(
                        rule="config-parse-error",
                        severity=Severity.WARNING,
                        message="expected a key=value line",
                        file="project.godot",
                        line=line_number,
                    )
                )
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            full_key = f"{section}/{key}" if section else key
            paths = extract_res_paths(value)
            if full_key == "application/run/main_scene" and paths:
                self.main_scene = paths[0]
            if section == "autoload" and paths:
                self.autoloads[key] = paths[0]
            line_offset = raw_line.find(line)
            if line_offset < 0:
                line_offset = 0
            value_offset = line.find(value)
            if value_offset < 0:
                value_offset = 0
            for raw_path in paths:
                self.refs.append(
                    Reference(
                        raw=raw_path,
                        file="project.godot",
                        line=line_number,
                        column=line_offset + value_offset + value.find(raw_path) + 1,
                        kind="project",
                        eager=False,
                    )
                )

    def resolve(self, raw: str) -> str | None:
        if not raw.startswith("res://"):
            return None
        relative = raw[len("res://") :]
        if relative.startswith("/"):
            relative = relative.lstrip("/")
        if not relative:
            return ""
        normalized = posixpath.normpath(relative)
        if normalized in ("", "."):
            return ""
        if normalized == ".." or normalized.startswith("../"):
            return None
        return normalized

    def case_insensitive_matches(self, relative: str) -> list[str]:
        return self.casefold.get(relative.casefold(), [])

    def is_directory(self, relative: str) -> bool:
        if not relative:
            return True
        return (self.root / relative).is_dir()
