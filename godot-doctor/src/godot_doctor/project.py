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


def bracket_delta(text: str) -> int:
    """Count { } [ ] outside quoted strings; Godot config values may span lines."""
    depth = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
    return depth


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
    unreadable_directories: set[str] = field(default_factory=set)

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
            self.unreadable_directories.add(relative or ".")
            self.scan_diagnostics.append(
                Diagnostic(
                    rule="unreadable-directory",
                    severity=Severity.WARNING,
                    message=f"cannot read this directory: {error.strerror or error}",
                    file=relative or ".",
                    anchor=relative or ".",
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
        depth = 0
        open_line: int | None = None
        open_anchor = ""
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            line = strip_comment(raw_line).strip()
            if depth > 0:
                # A value opened with { or [ continues until its brackets balance.
                depth = max(0, depth + bracket_delta(line))
                if depth == 0:
                    open_line = None
                    open_anchor = ""
                self._record_config_refs(raw_line, line, line_number)
                continue
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if line.startswith("#"):
                self.parse_diagnostics.append(
                    Diagnostic(
                        rule="config-hash-comment",
                        severity=Severity.ERROR,
                        message=(
                            "'#' is not a comment in Godot's config format; this line "
                            "is folded into the next key and can silently lose a setting"
                        ),
                        file="project.godot",
                        line=line_number,
                        anchor=line,
                    )
                )
                continue
            if "=" not in line:
                self.parse_diagnostics.append(
                    Diagnostic(
                        rule="config-nonkey-line",
                        severity=Severity.INFO,
                        message="expected a key=value line",
                        file="project.godot",
                        line=line_number,
                        anchor=line,
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
            self._record_config_refs(raw_line, line, line_number, value)
            depth = max(0, bracket_delta(value))
            if depth > 0:
                open_line = line_number
                open_anchor = line

        if depth > 0 and open_line is not None:
            self.parse_diagnostics.append(
                Diagnostic(
                    rule="config-unclosed-value",
                    severity=Severity.ERROR,
                    message=(
                        "unclosed value: Godot cannot parse the rest of project.godot"
                    ),
                    file="project.godot",
                    line=open_line,
                    anchor=open_anchor,
                )
            )

    def _record_config_refs(
        self, raw_line: str, line: str, line_number: int, value: str | None = None
    ) -> None:
        segment = line if value is None else value
        line_start = raw_line.find(line)
        if line_start < 0:
            line_start = 0
        segment_start = line_start
        if value is not None:
            value_offset = line.find(value)
            if value_offset >= 0:
                segment_start += value_offset
        for raw_path in extract_res_paths(segment):
            offset = segment.find(raw_path)
            if offset < 0:
                offset = 0
            self.refs.append(
                Reference(
                    raw=raw_path,
                    file="project.godot",
                    line=line_number,
                    column=segment_start + offset + 1,
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

    def is_directory(self, relative: str) -> bool | None:
        """None means the path could not be probed, which is not the same as absent."""
        if not relative:
            return True
        try:
            return (self.root / relative).is_dir()
        except OSError:
            # pathlib re-raises EACCES, unlike ENOENT.
            return None

    def is_under_unreadable(self, relative: str) -> bool:
        """True when the walk never indexed the directory holding this path."""
        for directory in self.unreadable_directories:
            if directory == "." or relative == directory or relative.startswith(directory + "/"):
                return True
        return False
