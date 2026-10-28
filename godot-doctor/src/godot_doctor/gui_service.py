from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .project import Project
from .report import format_json, format_text
from .rules import AnalysisResult, analyze


class GuiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def required_path(value: object) -> str:
    if not isinstance(value, str):
        raise GuiError("Choose a project directory or a file inside it.")
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1].strip()
    if not value:
        raise GuiError("Choose a project directory or a file inside it.")
    if len(value) > 4096 or "\x00" in value:
        raise GuiError("The supplied path is invalid.")
    return value


class GuiService:
    def __init__(self, initial_path: str = "."):
        self.initial_path = str(Path(initial_path).expanduser().absolute())
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._result: AnalysisResult | None = None
        self._allowed_files: set[str] = set()
        self._started = 0.0
        self._state: dict = {
            "status": "idle", "run_id": 0, "path": self.initial_path,
            "started_at": None, "finished_at": None, "elapsed_ms": 0,
            "error": None, "report": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
            if state["status"] == "running":
                state["elapsed_ms"] = round((time.perf_counter() - self._started) * 1000)
            return state

    def start(self, path: object) -> dict:
        path = required_path(path)
        with self._lock:
            if self._state["status"] == "running":
                raise GuiError("An analysis is already running.", 409)
            self._started = time.perf_counter()
            self._result = None
            self._allowed_files = set()
            self._state = {
                "status": "running", "run_id": self._state["run_id"] + 1,
                "path": path, "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None, "elapsed_ms": 0, "error": None, "report": None,
            }
            state = dict(self._state)
            self._thread = threading.Thread(target=self._scan, args=(path,), daemon=True)
            self._thread.start()
            return state

    def _scan(self, path: str) -> None:
        result = None
        report = None
        error = None
        allowed: set[str] = set()
        try:
            result = analyze(Project.load(path))
            report = json.loads(format_json(result))
            allowed = {row.file for row in result.diagnostics}
            allowed.update(name for row in result.diagnostics for name in row.related)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        with self._lock:
            self._result = result
            self._allowed_files = allowed
            self._state.update(
                status="failed" if error else "complete", error=error, report=report,
                path=str(result.project.root) if result else path,
                finished_at=datetime.now(timezone.utc).isoformat(),
                elapsed_ms=round((time.perf_counter() - self._started) * 1000),
            )

    def _current_result(self, run_id: int) -> AnalysisResult:
        if self._state["status"] != "complete" or self._result is None:
            raise GuiError("Run an analysis before inspecting or exporting results.", 409)
        if run_id != self._state["run_id"]:
            raise GuiError("These results belong to an earlier scan. Select the current result.", 409)
        return self._result

    def export(self, format_name: str, run_id: int) -> tuple[str, str, str]:
        if format_name not in ("json", "text"):
            raise GuiError("Report format must be json or text.")
        with self._lock:
            result = self._current_result(run_id)
        if format_name == "json":
            return format_json(result), "application/json", "godot-doctor-report.json"
        return format_text(result), "text/plain", "godot-doctor-report.txt"

    def source(self, file: str, line: int, run_id: int) -> dict:
        if line < 1:
            raise GuiError("Source line must be a positive integer.")
        with self._lock:
            result = self._current_result(run_id)
            if file not in self._allowed_files or file not in result.project.files:
                raise GuiError("This file is not an available source in the current report.", 404)
            root = result.project.root
            path = result.project.files[file]
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise GuiError("Source preview cannot leave the selected project.", 403)
            if resolved.suffix.lower() not in (
                ".gd", ".gdshader", ".gdshaderinc", ".tscn", ".tres",
                ".godot", ".uid", ".import",
            ):
                raise GuiError("This file format does not support a text preview.", 415)
            start, end = max(1, line - 7), line + 7
            lines: list[dict] = []
            more_after = False
            size = 0
            with resolved.open(encoding="utf-8-sig") as handle:
                for number, text in enumerate(handle, 1):
                    if number > end:
                        more_after = True
                        break
                    if number >= start:
                        size += len(text)
                        if size > 65536:
                            raise GuiError("This source excerpt is too large to preview.", 413)
                        lines.append({"number": number, "text": text.rstrip("\r\n")})
            if not lines or line > lines[-1]["number"]:
                raise GuiError("This source location no longer exists. Run the analysis again.", 409)
            return {
                "file": file, "line": line, "run_id": run_id, "lines": lines,
                "has_more_before": start > 1, "has_more_after": more_after,
            }
        except (OSError, UnicodeError) as exc:
            raise GuiError(f"Cannot preview this source: {exc}", 404) from exc

    def browse(self, path: object) -> dict:
        try:
            directory = Path(required_path(path)).expanduser().resolve(strict=True)
            if directory.is_file():
                directory = directory.parent
            if not directory.is_dir():
                raise GuiError("Choose a directory.")
            children = []
            unreadable = 0
            for child in sorted(directory.iterdir(), key=lambda entry: (entry.name.casefold(), entry.name)):
                try:
                    if not child.is_dir():
                        continue
                except OSError:
                    unreadable += 1
                    continue
                entry = {"name": child.name, "path": str(child), "is_project": False}
                try:
                    entry["is_project"] = (child / "project.godot").is_file()
                except OSError:
                    entry["accessible"] = False
                    unreadable += 1
                children.append(entry)
            return {
                "path": str(directory),
                "parent": str(directory.parent) if directory.parent != directory else None,
                "is_project": (directory / "project.godot").is_file(),
                "directories": children, "unreadable_count": unreadable,
            }
        except (OSError, RuntimeError) as exc:
            raise GuiError(f"Cannot open this directory: {exc}", 400) from exc
