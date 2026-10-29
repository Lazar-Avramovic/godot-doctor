from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Sequence

from .formats import SCHEMA_VERSION, TOOL
from .model import Reference
from .parsers import ParsedFile
from .project import IGNORED_DIRS, Project, strip_comment
from .rulebook import RULEBOOK
from .rules import AnalysisResult, build_dependency_graph, find_cycles, uid_number

MAX_EXCERPT_CHARS = 65536
REFERENCE_KINDS = (
    "project",
    "ext_resource",
    "property",
    "preload",
    "load",
    "change_scene_to_file",
)
REFERENCE_STATUSES = ("ok", "missing", "case-mismatch", "directory", "invalid", "root")
FILE_KINDS = ("scene", "resource", "script", "uid", "import")


class InspectError(Exception):
    """Raised when a CLI inspection request cannot be satisfied."""


def _schema_payload(**fields: object) -> dict:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "tool": dict(TOOL),
    }
    payload.update(fields)
    return payload


def normalize_project_file(project: Project, file: str) -> str:
    candidate = Path(file).expanduser()
    root = project.root.resolve()
    target = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise InspectError(f"cannot read {file}: {exc}") from exc
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise InspectError(f"{file!r} is outside the selected project") from exc
    if relative not in project.files:
        matches = project.case_insensitive_matches(relative)
        if not matches:
            raise InspectError(f"{relative!r} is not an indexed file in this project")
        relative = matches[0]
    if project.files[relative].resolve() != resolved:
        raise InspectError(f"{relative!r} does not map to the selected project file")
    return relative


def source_excerpt(
    project: Project, file: str, line: int = 1, radius: int = 7
) -> dict:
    if line < 1:
        raise InspectError("line must be a positive integer")
    if radius < 0 or radius > 1000:
        raise InspectError("radius must be between 0 and 1000")
    relative = normalize_project_file(project, file)
    path = project.files[relative]
    start = max(1, line - radius)
    end = line + radius
    lines: list[dict[str, object]] = []
    size = 0
    more_after = False
    last = 0
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for number, text in enumerate(handle, 1):
                if "\x00" in text:
                    raise InspectError(f"{relative!r} is a binary file")
                last = number
                if number > end:
                    more_after = True
                    break
                if number >= start:
                    size += len(text)
                    if size > MAX_EXCERPT_CHARS:
                        raise InspectError("this source excerpt is too large to preview")
                    lines.append({"number": number, "text": text.rstrip("\r\n")})
    except (OSError, UnicodeError) as exc:
        raise InspectError(f"cannot read {relative!r} as UTF-8 text: {exc}") from exc
    if not lines or line > last:
        raise InspectError(
            f"line {line} does not exist in {relative!r} ({last} line(s))"
        )
    return {
        "file": relative,
        "line": line,
        "start_line": start,
        "end_line": min(end, last),
        "lines": lines,
        "has_more_before": start > 1,
        "has_more_after": more_after,
    }


def render_excerpt(project: Project, excerpt: dict) -> str:
    width = len(str(excerpt["lines"][-1]["number"]))
    rows = [
        f"{excerpt['file']}:{excerpt['line']}",
        *(
            f"{str(row['number']).rjust(width)} | {row['text']}"
            for row in excerpt["lines"]
        ),
    ]
    markers = []
    if excerpt["has_more_before"]:
        markers.append("earlier lines")
    if excerpt["has_more_after"]:
        markers.append("later lines")
    if markers:
        rows.append(f"[{' and '.join(markers)} not shown]")
    return "\n".join(rows)


def browse_directory(path: str) -> dict:
    try:
        directory = Path(path).expanduser().resolve(strict=True)
        if directory.is_file():
            directory = directory.parent
        if not directory.is_dir():
            raise InspectError("choose a directory")
        children = []
        unreadable = 0
        for child in sorted(
            directory.iterdir(), key=lambda entry: (entry.name.casefold(), entry.name)
        ):
            try:
                if not child.is_dir():
                    continue
            except OSError:
                unreadable += 1
                continue
            entry: dict[str, object] = {
                "name": child.name,
                "path": str(child),
                "is_project": False,
            }
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
            "directories": children,
            "unreadable_count": unreadable,
        }
    except InspectError:
        raise
    except (OSError, RuntimeError) as exc:
        raise InspectError(f"cannot open this directory: {exc}") from exc


def render_browse(listing: dict) -> str:
    lines = [
        f"{'*' if row.get('is_project') else ' '} {row['path']}"
        for row in listing["directories"]
    ]
    if not lines:
        lines.append("(no subdirectories)")
    if listing["unreadable_count"]:
        count = listing["unreadable_count"]
        lines.append(f"{count} unreadable {'entry' if count == 1 else 'entries'}.")
    return "\n".join(lines)


def _project_meta(config_path: Path) -> tuple[str | None, str | None]:
    name = None
    version = None
    try:
        text = config_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None, None
    section = ""
    for raw_line in text.splitlines():
        line = strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "config_version":
            version = value or None
        elif section == "application" and key == "config/name":
            name = value.strip('"').strip("'") or None
    return name, version


def discover_projects(
    path: str, depth: int | None = None, limit: int | None = None
) -> dict:
    if depth is not None and depth < 0:
        raise InspectError("depth must be zero or a positive integer")
    if limit is not None and limit < 1:
        raise InspectError("limit must be a positive integer")
    root = Path(path).expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise InspectError(f"path does not exist: {path}") from exc
    if root.is_file():
        root = root.parent
    if not root.is_dir():
        raise InspectError(f"not a directory: {path}")

    projects: list[dict[str, object]] = []
    unreadable = 0
    truncated = False

    def record_unreadable(error: OSError) -> None:
        nonlocal unreadable
        unreadable += 1

    for dirpath, dirnames, filenames in os.walk(root, onerror=record_unreadable):
        current = Path(dirpath)
        try:
            relative_depth = len(current.relative_to(root).parts)
        except ValueError:
            continue
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in IGNORED_DIRS and not name.startswith(".")
        )
        if depth is not None and relative_depth >= depth:
            dirnames[:] = []
        if "project.godot" in filenames:
            relative = current.relative_to(root).as_posix()
            name, version = _project_meta(current / "project.godot")
            projects.append(
                {
                    "name": name or current.name,
                    "path": str(current),
                    "relative": "." if relative == "." else relative,
                    "config_version": version,
                }
            )
            if limit is not None and len(projects) >= limit:
                truncated = True
                projects = projects[:limit]
                break
    projects.sort(key=lambda item: str(item["path"]).casefold())
    return {
        "root": str(root),
        "projects": projects,
        "count": len(projects),
        "unreadable_count": unreadable,
        "truncated": truncated,
        "depth": depth,
    }


def render_discover(payload: dict) -> str:
    lines = [str(row["path"]) for row in payload["projects"]]
    if payload["truncated"]:
        lines.append("(results truncated by --limit)")
    return "\n".join(lines)


def _resolve_reference(project: Project, reference: Reference) -> dict[str, object]:
    resolved = project.resolve(reference.raw)
    status = "ok"
    actual = None
    if resolved is None:
        status = "invalid"
    elif resolved == "":
        status = "root"
    elif resolved not in project.files:
        matches = project.case_insensitive_matches(resolved)
        if matches:
            status = "case-mismatch"
            actual = matches[0]
        elif project.is_directory(resolved):
            status = "directory"
        else:
            status = "missing"
    return {
        "file": reference.file,
        "line": reference.line,
        "column": reference.column,
        "kind": reference.kind,
        "eager": reference.eager,
        "raw": reference.raw,
        "target": resolved,
        "status": status,
        "actual": actual,
    }


def references_payload(
    project: Project,
    parsed: dict[str, ParsedFile],
    *,
    unresolved: bool = False,
    eager: bool = False,
    kinds: Sequence[str] | None = None,
) -> dict[str, object]:
    references = list(project.refs)
    for parsed_file in parsed.values():
        references.extend(parsed_file.refs)
    rows = [_resolve_reference(project, reference) for reference in references]
    if unresolved:
        rows = [row for row in rows if row["status"] != "ok"]
    if eager:
        rows = [row for row in rows if row["eager"]]
    if kinds:
        wanted = set(kinds)
        rows = [row for row in rows if row["kind"] in wanted]
    rows.sort(
        key=lambda row: (
            str(row["file"]),
            row["line"] if isinstance(row["line"], int) else 0,
            row["column"] if isinstance(row["column"], int) else 0,
            str(row["raw"]),
            str(row["kind"]),
        )
    )
    status_counts = Counter({status: 0 for status in REFERENCE_STATUSES})
    status_counts.update(str(row["status"]) for row in rows)
    kind_counts = Counter({kind: 0 for kind in REFERENCE_KINDS})
    kind_counts.update(str(row["kind"]) for row in rows)
    return _schema_payload(
        project=str(project.root),
        summary={
            "total": len(rows),
            "eager": sum(1 for row in rows if row["eager"]),
            "unresolved": sum(1 for row in rows if row["status"] != "ok"),
            "by_status": dict(sorted(status_counts.items())),
            "by_kind": dict(sorted(kind_counts.items())),
        },
        references=rows,
    )


def render_references(payload: dict) -> str:
    lines = []
    for row in payload["references"]:
        location = f"{row['file']}:{row['line']}:{row['column']}"
        line = f"{location}: {row['kind']} [{row['status']}] {row['raw']}"
        if row["status"] == "case-mismatch":
            line += f" -> {row['actual']}"
        elif row["status"] != "ok" and row["target"]:
            line += f" -> {row['target']}"
        lines.append(line)
    if not lines:
        lines.append("No references found.")
    summary = payload["summary"]
    lines.append("")
    lines.append(
        f"{summary['total']} reference(s), {summary['eager']} eager, "
        f"{summary['unresolved']} unresolved."
    )
    return "\n".join(lines)


def graph_payload(project: Project, parsed: dict[str, ParsedFile]) -> dict[str, object]:
    graph = build_dependency_graph(project, parsed)
    edge_lines: dict[tuple[str, str], int] = {}
    for parsed_file in parsed.values():
        for reference in parsed_file.refs:
            if not reference.eager:
                continue
            target = project.resolve(reference.raw)
            if target and target in parsed and target != parsed_file.path:
                edge_lines.setdefault((parsed_file.path, target), reference.line)
    edges = [
        {"from": source, "to": target, "line": edge_lines.get((source, target))}
        for source in sorted(graph)
        for target in sorted(graph[source])
    ]
    incoming = Counter(edge["to"] for edge in edges)
    outgoing = Counter(edge["from"] for edge in edges)
    nodes = [
        {
            "id": rel,
            "kind": parsed_file.kind,
            "uid": parsed_file.uid,
            "eager_out": outgoing[rel],
            "eager_in": incoming[rel],
        }
        for rel, parsed_file in sorted(parsed.items())
    ]
    cycles = []
    for cycle in find_cycles(graph):
        cycle_nodes = cycle[:-1] if cycle and cycle[0] == cycle[-1] else list(cycle)
        if not cycle_nodes:
            continue
        cycles.append(
            {
                "nodes": cycle_nodes,
                "display": " -> ".join([*cycle_nodes, cycle_nodes[0]]),
            }
        )
    return _schema_payload(
        project=str(project.root),
        summary={
            "nodes": len(nodes),
            "edges": len(edges),
            "cycles": len(cycles),
            "roots": sum(1 for node in nodes if not node["eager_in"]),
            "leaves": sum(1 for node in nodes if not node["eager_out"]),
        },
        nodes=nodes,
        edges=edges,
        cycles=cycles,
    )


def render_graph_text(payload: dict) -> str:
    lines = [
        f"{edge['from']} -> {edge['to']}"
        + (f" (line {edge['line']})" if edge["line"] else "")
        for edge in payload["edges"]
    ]
    for cycle in payload["cycles"]:
        lines.append(f"cycle: {cycle['display']}")
    if not lines:
        lines.append("No eager dependency edges.")
    summary = payload["summary"]
    lines.append("")
    lines.append(
        f"{summary['nodes']} node(s), {summary['edges']} edge(s), "
        f"{summary['cycles']} cycle(s)."
    )
    return "\n".join(lines)


def _dot_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_graph_dot(payload: dict) -> str:
    lines = ["digraph godot_project {", "  rankdir=LR;"]
    for node in payload["nodes"]:
        lines.append(f"  {_dot_quote(str(node['id']))};")
    for edge in payload["edges"]:
        lines.append(
            f"  {_dot_quote(str(edge['from']))} -> {_dot_quote(str(edge['to']))};"
        )
    lines.append("}")
    return "\n".join(lines)


def stats_payload(
    result: AnalysisResult, parsed: dict[str, ParsedFile], top: int = 10
) -> dict[str, object]:
    diagnostics = result.diagnostics
    by_rule = Counter({rule_id: 0 for rule_id in RULEBOOK})
    by_rule.update(item.rule for item in diagnostics)
    by_severity = Counter(item.severity.value for item in diagnostics)
    by_directory = Counter(
        item.file.split("/")[0] if "/" in item.file else "."
        for item in diagnostics
    )
    file_counts = Counter(item.file for item in diagnostics)
    top_files = [
        {"file": name, "count": count}
        for name, count in sorted(file_counts.items(), key=lambda pair: (-pair[1], pair[0]))[
            :top
        ]
    ]
    kinds = Counter({kind: 0 for kind in FILE_KINDS})
    kinds.update(parsed_file.kind for parsed_file in parsed.values())
    references = references_payload(result.project, parsed)
    reference_summary = references["summary"]
    uid_values = [
        parsed_file.uid for parsed_file in parsed.values() if parsed_file.uid
    ]
    uid_numbers = {
        number
        for number in (uid_number(value) for value in uid_values)
        if number is not None
    }
    duplicate_groups = sum(1 for item in diagnostics if item.rule == "duplicate-uid")
    return _schema_payload(
        project=str(result.project.root),
        files={
            "indexed": result.files_indexed,
            "analyzed": result.files_analyzed,
            "by_kind": dict(sorted(kinds.items())),
        },
        diagnostics={
            "total": len(diagnostics),
            "errors": by_severity.get("error", 0),
            "warnings": by_severity.get("warning", 0),
            "infos": by_severity.get("info", 0),
            "by_rule": dict(sorted(by_rule.items())),
            "by_directory": dict(sorted(by_directory.items())),
            "files_with_diagnostics": len(file_counts),
            "top_files": top_files,
        },
        references={
            "total": reference_summary["total"],
            "eager": reference_summary["eager"],
            "unresolved": reference_summary["unresolved"],
            "by_status": reference_summary["by_status"],
            "by_kind": reference_summary["by_kind"],
        },
        uids={
            "declared": len(uid_values),
            "invalid": len(uid_values) - len(uid_numbers),
            "distinct": len(uid_numbers),
            "duplicate_groups": duplicate_groups,
        },
    )


def render_stats(payload: dict) -> str:
    files = payload["files"]
    diagnostics = payload["diagnostics"]
    references = payload["references"]
    uids = payload["uids"]
    kinds = ", ".join(
        f"{name} {count}" for name, count in files["by_kind"].items() if count
    )
    kind_text = f" ({kinds})" if kinds else ""
    rule_text = ", ".join(
        f"{rule} {count}"
        for rule, count in diagnostics["by_rule"].items()
        if count
    )
    status_text = ", ".join(
        f"{name} {count}"
        for name, count in references["by_status"].items()
        if count
    )
    return "\n".join(
        [
            f"Project: {payload['project']}",
            f"Files: {files['indexed']} indexed, {files['analyzed']} analyzed{kind_text}",
            f"Diagnostics: {diagnostics['errors']} error(s), "
            f"{diagnostics['warnings']} warning(s), {diagnostics['infos']} info(s)"
            + (f"; {rule_text}" if rule_text else ""),
            f"References: {references['total']} total, {references['eager']} eager, "
            f"{references['unresolved']} unresolved"
            + (f" ({status_text})" if status_text else ""),
            f"UIDs: {uids['declared']} declared ({uids['invalid']} invalid), "
            f"{uids['distinct']} distinct, "
            f"{uids['duplicate_groups']} duplicate group(s)",
        ]
    )


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=True)
