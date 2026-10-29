from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .model import Diagnostic, Reference, Severity
from .parsers import ParsedFile, parse_gdscript_file, parse_scene_file, parse_uid_file
from .project import Project

UID_SIDECAR_SUFFIXES = (".gd.uid", ".gdshader.uid", ".gdshaderinc.uid")


@dataclass
class AnalysisResult:
    project: Project
    diagnostics: list[Diagnostic]
    files_indexed: int
    files_analyzed: int

    def _count(self, severity: Severity) -> int:
        return sum(1 for diagnostic in self.diagnostics if diagnostic.severity == severity)

    @property
    def error_count(self) -> int:
        return self._count(Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return self._count(Severity.WARNING)

    @property
    def info_count(self) -> int:
        return self._count(Severity.INFO)


def parse_all(project: Project) -> dict[str, ParsedFile]:
    parsed: dict[str, ParsedFile] = {}
    for rel in sorted(project.files):
        if rel.endswith((".tscn", ".tres")):
            parsed_file = parse_scene_file(project, rel)
        elif rel.endswith(".gd"):
            parsed_file = parse_gdscript_file(project, rel)
        elif rel.endswith(UID_SIDECAR_SUFFIXES) and rel[:-4] in project.files:
            parsed_file = parse_uid_file(project, rel)
        elif rel.endswith(".import") and rel[:-7] in project.files:
            parsed_file = parse_uid_file(project, rel)
        else:
            continue
        parsed[rel] = parsed_file
    return parsed


def build_dependency_graph(
    project: Project, parsed: dict[str, ParsedFile]
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {rel: set() for rel in parsed}
    for rel, parsed_file in parsed.items():
        for raw_path in parsed_file.eager_edges:
            target = project.resolve(raw_path)
            if target and target in parsed and target != rel:
                graph[rel].add(target)
    return graph


def analyze_parsed(
    project: Project, parsed: dict[str, ParsedFile]
) -> AnalysisResult:
    diagnostics = [*project.scan_diagnostics, *project.parse_diagnostics]
    for parsed_file in parsed.values():
        diagnostics.extend(parsed_file.diagnostics)

    diagnostics.extend(_check_references(project, parsed))
    diagnostics.extend(_check_cycles(project, parsed))
    diagnostics.extend(_check_duplicate_uids(parsed))

    return AnalysisResult(
        project=project,
        diagnostics=_dedupe(diagnostics),
        files_indexed=len(project.files),
        files_analyzed=len(parsed),
    )


def analyze(project: Project) -> AnalysisResult:
    return analyze_parsed(project, parse_all(project))


def _check_references(
    project: Project, parsed: dict[str, ParsedFile]
) -> list[Diagnostic]:
    references: list[Reference] = list(project.refs)
    for parsed_file in parsed.values():
        references.extend(parsed_file.refs)

    diagnostics: list[Diagnostic] = []
    for reference in references:
        relative = project.resolve(reference.raw)
        if relative is None:
            diagnostics.append(
                Diagnostic(
                    rule="invalid-resource-path",
                    severity=Severity.ERROR,
                    message=f"{reference.raw!r} escapes the project root",
                    file=reference.file,
                    line=reference.line,
                    column=reference.column,
                    anchor=reference.raw,
                )
            )
            continue
        if not relative:
            diagnostics.append(
                Diagnostic(
                    rule="resource-is-directory",
                    severity=Severity.ERROR,
                    message=f"{reference.raw!r} refers to the project root, not a file",
                    file=reference.file,
                    line=reference.line,
                    column=reference.column,
                    anchor=reference.raw,
                )
            )
            continue
        if relative in project.files:
            continue

        # The directory could not be listed, so its files are absent from the index:
        # absence there is not evidence, and the scan already warns about it. This
        # must precede the filesystem probes, which can raise on an unreadable parent.
        if project.is_under_unreadable(relative):
            continue

        case_matches = project.case_insensitive_matches(relative)
        if case_matches:
            actual = case_matches[0]
            diagnostics.append(
                Diagnostic(
                    rule="case-mismatch",
                    severity=Severity.WARNING,
                    message=(
                        f"{reference.raw!r} matches {actual!r} only on a "
                        "case-insensitive filesystem"
                    ),
                    file=reference.file,
                    line=reference.line,
                    column=reference.column,
                    related=(actual,),
                    anchor=reference.raw,
                )
            )
            continue

        is_directory = project.is_directory(relative)
        if is_directory is None:
            continue
        if is_directory:
            diagnostics.append(
                Diagnostic(
                    rule="resource-is-directory",
                    severity=Severity.ERROR,
                    message=f"{reference.raw!r} is a directory, not a resource file",
                    file=reference.file,
                    line=reference.line,
                    column=reference.column,
                    anchor=reference.raw,
                )
            )
            continue

        diagnostics.append(
            Diagnostic(
                rule="broken-reference",
                severity=Severity.ERROR,
                message=f"{reference.raw!r} does not exist",
                file=reference.file,
                line=reference.line,
                column=reference.column,
                anchor=reference.raw,
            )
        )
    return diagnostics


def uid_number(uid: str | None) -> int | None:
    if uid is None or not uid.startswith("uid://"):
        return None
    number = 0
    for char in uid[6:]:
        if "a" <= char <= "z":
            digit = ord(char) - ord("a")
        elif "0" <= char <= "9":
            digit = ord(char) - ord("0") + 25
        else:
            return None
        # Godot uses base 34, overlapping z/0 digits, and a 63-bit result.
        number = (number * 34 + digit) & 0x7FFFFFFFFFFFFFFF
    return number


def _check_duplicate_uids(parsed: dict[str, ParsedFile]) -> list[Diagnostic]:
    declarations: dict[int, list[ParsedFile]] = {}
    for parsed_file in parsed.values():
        number = uid_number(parsed_file.uid)
        if number is not None:
            declarations.setdefault(number, []).append(parsed_file)

    diagnostics: list[Diagnostic] = []
    for files in declarations.values():
        if len(files) < 2:
            continue
        files.sort(key=lambda item: item.path)
        first = files[0]
        paths = tuple(item.path for item in files)
        diagnostics.append(
            Diagnostic(
                rule="duplicate-uid",
                severity=Severity.ERROR,
                message=f"UID {first.uid!r} resolves to the same ID in: {', '.join(paths)}",
                file=first.path,
                line=first.uid_line,
                column=first.uid_column,
                related=paths[1:],
                anchor=first.uid or "|".join(paths),
            )
        )
    return diagnostics


def _check_cycles(
    project: Project, parsed: dict[str, ParsedFile]
) -> list[Diagnostic]:
    graph = build_dependency_graph(project, parsed)

    diagnostics: list[Diagnostic] = []
    for cycle in find_cycles(graph):
        nodes = cycle[:-1]
        if not nodes:
            continue
        display = " -> ".join([*nodes, nodes[0]])
        diagnostics.append(
            Diagnostic(
                rule="dependency-cycle",
                severity=Severity.WARNING,
                message=f"eager dependency cycle: {display}",
                file=nodes[0],
                related=tuple(nodes[1:]),
                anchor="|".join(sorted(nodes)),
            )
        )
    return diagnostics


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    color: dict[str, int] = {}
    position: dict[str, int] = {}
    cycles: list[list[str]] = []
    seen: set[frozenset[str]] = set()

    for start in sorted(graph):
        if color.get(start, 0) != 0:
            continue
        color[start] = 1
        position[start] = 0
        path = [start]
        stack: list[tuple[str, Iterator[str]]] = [
            (start, iter(sorted(graph.get(start, ()))))
        ]
        while stack:
            node, neighbours = stack[-1]
            pushed = False
            for neighbour in neighbours:
                state = color.get(neighbour, 0)
                if state == 0:
                    color[neighbour] = 1
                    position[neighbour] = len(path)
                    path.append(neighbour)
                    stack.append(
                        (neighbour, iter(sorted(graph.get(neighbour, ()))))
                    )
                    pushed = True
                    break
                if state == 1:
                    cycle_start = position[neighbour]
                    cycle = path[cycle_start:] + [neighbour]
                    key = frozenset(cycle[:-1])
                    if key not in seen:
                        seen.add(key)
                        cycles.append(cycle)
            if not pushed:
                color[node] = 2
                position.pop(node, None)
                stack.pop()
                path.pop()
    return cycles


def _dedupe(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[object, ...]] = set()
    unique: list[Diagnostic] = []
    for diagnostic in sorted(diagnostics, key=lambda item: item.sort_key()):
        key = (
            diagnostic.rule,
            diagnostic.severity,
            diagnostic.file,
            diagnostic.line,
            diagnostic.column,
            diagnostic.message,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(diagnostic)
    return unique
