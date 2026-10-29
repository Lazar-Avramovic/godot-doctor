from __future__ import annotations

import codecs
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .project import Project

CONFIG_FILE = "project.godot"


class FixError(Exception):
    """Raised when a planned fix can no longer be applied safely."""


@dataclass(frozen=True)
class FixChange:
    file: str
    line: int
    before: str
    after: str

    def to_dict(self) -> dict[str, object]:
        return {
            "line": self.line,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class SkippedFix:
    file: str
    line: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"line": self.line, "reason": self.reason}


@dataclass(frozen=True)
class FixPlan:
    project: Project
    changes: tuple[FixChange, ...] = ()
    skipped: tuple[SkippedFix, ...] = ()

    @property
    def files(self) -> list[str]:
        return sorted({change.file for change in self.changes})


@dataclass(frozen=True)
class AppliedFixes:
    changes: tuple[FixChange, ...] = ()

    @property
    def files(self) -> list[str]:
        return sorted({change.file for change in self.changes})


def plan_payload(plan: FixPlan) -> dict[str, object]:
    return {
        "summary": {
            "files": len(plan.files),
            "lines": len(plan.changes),
            "skipped": len(plan.skipped),
        },
        "files": [
            {
                "file": name,
                "changes": [
                    change.to_dict()
                    for change in plan.changes
                    if change.file == name
                ],
            }
            for name in plan.files
        ],
        "skipped": [
            {"file": skipped.file, **skipped.to_dict()}
            for skipped in plan.skipped
        ],
    }


def _split_ending(line: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def _decode(raw: bytes, name: str) -> tuple[str, bool]:
    bom = raw.startswith(codecs.BOM_UTF8)
    try:
        return raw.decode("utf-8-sig"), bom
    except UnicodeError as exc:
        raise FixError(f"cannot read {name} as UTF-8: {exc}") from exc


def _encode(text: str, bom: bool) -> bytes:
    return (codecs.BOM_UTF8 if bom else b"") + text.encode("utf-8")


def plan_config_fixes(project: Project) -> FixPlan:
    """Plan the repair of '#' comment lines that Godot's ConfigFile folds into a key.

    Only lines the analyzer already flagged as config-parse-error are touched, and only
    when they begin with '#'. Anything else needs a human decision.
    """
    lines = [
        diagnostic
        for diagnostic in project.parse_diagnostics
        if diagnostic.rule == "config-hash-comment"
        and diagnostic.file == CONFIG_FILE
        and diagnostic.line is not None
    ]
    if not lines:
        return FixPlan(project=project)

    path = project.root / CONFIG_FILE
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FixError(f"cannot read {CONFIG_FILE}: {exc}") from exc
    text, _bom = _decode(raw, CONFIG_FILE)
    source_lines = text.splitlines(keepends=True)

    changes: list[FixChange] = []
    skipped: list[SkippedFix] = []
    seen: set[int] = set()
    for diagnostic in lines:
        number = diagnostic.line
        assert number is not None
        if number in seen:
            continue
        seen.add(number)
        if not 1 <= number <= len(source_lines):
            skipped.append(
                SkippedFix(
                    CONFIG_FILE,
                    number,
                    "this line no longer exists; run a new scan",
                )
            )
            continue
        content, _ending = _split_ending(source_lines[number - 1])
        stripped = content.lstrip()
        if not stripped.startswith("#"):
            skipped.append(
                SkippedFix(
                    CONFIG_FILE,
                    number,
                    "this line changed since the scan; run a new scan",
                )
            )
            continue
        indent = content[: len(content) - len(stripped)]
        after = f"{indent};{stripped[1:]}"
        changes.append(FixChange(CONFIG_FILE, number, content, after))

    return FixPlan(project=project, changes=tuple(changes), skipped=tuple(skipped))


def _write_file(path: Path, data: bytes) -> None:
    if path.is_symlink():
        path.write_bytes(data)
        return
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            # The temp inherited the target's mode, so a read-only target leaves a
            # read-only temp that Windows refuses to unlink.
            try:
                os.chmod(temporary, stat.S_IWRITE)
                os.unlink(temporary)
            except OSError:
                pass
        raise


def apply_fixes(plan: FixPlan) -> AppliedFixes:
    """Write a plan after verifying every target line is byte-for-byte what was previewed."""
    if not plan.changes:
        return AppliedFixes()

    root = plan.project.root.resolve()
    for relative in plan.files:
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise FixError(f"refusing to write outside the project: {relative}")
        grouped = [change for change in plan.changes if change.file == relative]
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FixError(f"cannot read {relative}: {exc}") from exc
        text, bom = _decode(raw, relative)
        lines = text.splitlines(keepends=True)
        for change in grouped:
            if not 1 <= change.line <= len(lines):
                raise FixError(
                    f"{relative}:{change.line} no longer exists; run a new scan"
                )
            content, _ending = _split_ending(lines[change.line - 1])
            if content != change.before:
                raise FixError(
                    f"{relative}:{change.line} changed since the preview; "
                    "run a new scan and preview again"
                )
            lines[change.line - 1] = change.after + _ending
        try:
            _write_file(path, _encode("".join(lines), bom))
        except OSError as exc:
            raise FixError(f"cannot write {relative}: {exc}") from exc

    return AppliedFixes(changes=plan.changes)