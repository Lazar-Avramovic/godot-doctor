from __future__ import annotations

import bisect
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from .model import Diagnostic, Reference, Severity
from .project import Project, extract_res_paths, strip_comment

ATTRIBUTE_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^\s\]]+))')
EXT_RESOURCE_REF_RE = re.compile(r'(?<![\w])ExtResource\("([^"]+)"\)')
SUB_RESOURCE_REF_RE = re.compile(r'(?<![\w])SubResource\("([^"]+)"\)')


@dataclass
class ExtResource:
    id: str
    path: str | None
    type: str | None
    uid: str | None
    line: int


@dataclass
class ParsedFile:
    path: str
    kind: str
    uid: str | None = None
    uid_line: int | None = None
    uid_column: int | None = None
    ext_resources: dict[str, ExtResource] = field(default_factory=dict)
    sub_resource_ids: set[str] = field(default_factory=set)
    refs: list[Reference] = field(default_factory=list)
    eager_edges: set[str] = field(default_factory=set)
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


def _parse_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTRIBUTE_RE.finditer(text):
        attrs[match.group(1)] = (
            match.group(2) if match.group(2) is not None else match.group(3)
        )
    return attrs


def _section(line: str) -> tuple[str, dict[str, str]] | None:
    stripped = strip_comment(line).strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    inner = stripped[1:-1].strip()
    if not inner:
        return None
    name, _, rest = inner.partition(" ")
    return name, _parse_attrs(rest)


def _scene_content_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield lines that start outside multi-line strings, with comments removed."""
    quote: str | None = None
    escaped = False
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        continuation = quote is not None
        current: list[str] = []
        for char in raw_line:
            if escaped:
                current.append(char)
                escaped = False
            elif quote is not None:
                current.append(char)
                if char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char == ";":
                break
            elif char == "\\":
                current.append(char)
                escaped = True
            else:
                current.append(char)
                if char in ('"', "'"):
                    quote = char
        if not continuation:
            yield line_number, "".join(current)


def _mask_prose_strings(line: str) -> str:
    """Blank quoted prose while preserving ExtResource/SubResource arguments."""
    chars = list(line)
    index = 0
    while index < len(line):
        if line[index] not in ('"', "'"):
            index += 1
            continue
        start = index
        quote = line[index]
        index += 1
        while index < len(line):
            if line[index] == "\\" and index + 1 < len(line):
                index += 2
                continue
            if line[index] == quote:
                index += 1
                break
            index += 1
        prefix = line[:start].rstrip()
        if prefix.endswith("ExtResource(") or prefix.endswith("SubResource("):
            continue
        for position in range(start, min(index, len(line))):
            chars[position] = " "
    return "".join(chars)


def parse_scene_file(project: Project, rel: str) -> ParsedFile:
    parsed = ParsedFile(
        path=rel,
        kind="scene" if rel.endswith(".tscn") else "resource",
    )
    try:
        text = project.files[rel].read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        parsed.diagnostics.append(
            Diagnostic(
                rule="parse-error",
                severity=Severity.ERROR,
                message=f"cannot read file: {exc}",
                file=rel,
                anchor="read-error",
            )
        )
        return parsed

    used_ext: dict[str, int] = {}
    used_sub: dict[str, int] = {}
    header_pending = True

    for line_number, line in _scene_content_lines(text):
        section = _section(line)
        if header_pending and line.strip():
            header_pending = False
            if section is not None and section[0] in ("gd_scene", "gd_resource"):
                for match in ATTRIBUTE_RE.finditer(line):
                    if match.group(1) == "uid":
                        value_group = 2 if match.group(2) is not None else 3
                        parsed.uid = match.group(value_group)
                        parsed.uid_line = line_number
                        parsed.uid_column = match.start(value_group) + 1
        if section is not None:
            name, attrs = section
            if name == "ext_resource":
                ext_id = attrs.get("id")
                if not ext_id:
                    parsed.diagnostics.append(
                        Diagnostic(
                            rule="parse-error",
                            severity=Severity.ERROR,
                            message="ext_resource is missing an id",
                            file=rel,
                            line=line_number,
                            anchor="ext_resource:missing-id",
                        )
                    )
                    continue
                if ext_id in parsed.ext_resources:
                    parsed.diagnostics.append(
                        Diagnostic(
                            rule="duplicate-ext-resource",
                            severity=Severity.ERROR,
                            message=f"duplicate ext_resource id {ext_id!r}",
                            file=rel,
                            line=line_number,
                            anchor=ext_id,
                        )
                    )
                    continue
                ext_path = attrs.get("path")
                if not ext_path:
                    parsed.diagnostics.append(
                        Diagnostic(
                            rule="parse-error",
                            severity=Severity.ERROR,
                            message=f"ext_resource {ext_id!r} is missing a path",
                            file=rel,
                            line=line_number,
                            anchor=ext_id,
                        )
                    )
                    continue
                parsed.ext_resources[ext_id] = ExtResource(
                    id=ext_id,
                    path=ext_path,
                    type=attrs.get("type"),
                    uid=attrs.get("uid"),
                    line=line_number,
                )
                if ext_path.startswith("res://"):
                    parsed.refs.append(
                        Reference(
                            raw=ext_path,
                            file=rel,
                            line=line_number,
                            column=line.find(ext_path) + 1,
                            kind="ext_resource",
                            eager=True,
                        )
                    )
                    parsed.eager_edges.add(ext_path)
                else:
                    parsed.diagnostics.append(
                        Diagnostic(
                            rule="invalid-ext-resource",
                            severity=Severity.WARNING,
                            message=(
                                f"ext_resource {ext_id!r} does not use a res:// path"
                            ),
                            file=rel,
                            line=line_number,
                            anchor=ext_id,
                        )
                    )
            elif name == "node":
                instance = attrs.get("instance")
                if instance:
                    match = re.fullmatch(r'ExtResource\("([^"]+)"\)', instance)
                    if match:
                        used_ext.setdefault(match.group(1), line_number)
                    else:
                        parsed.diagnostics.append(
                            Diagnostic(
                                rule="parse-error",
                                severity=Severity.WARNING,
                                message=f"unrecognised instance reference {instance!r}",
                                file=rel,
                                line=line_number,
                                anchor=instance,
                            )
                        )
            elif name == "sub_resource":
                sub_id = attrs.get("id")
                if sub_id:
                    parsed.sub_resource_ids.add(sub_id)
            continue

        reference_line = _mask_prose_strings(line)
        for match in EXT_RESOURCE_REF_RE.finditer(reference_line):
            used_ext.setdefault(match.group(1), line_number)
        for match in SUB_RESOURCE_REF_RE.finditer(reference_line):
            used_sub.setdefault(match.group(1), line_number)
        for raw_path in extract_res_paths(line):
            parsed.refs.append(
                Reference(
                    raw=raw_path,
                    file=rel,
                    line=line_number,
                    column=line.find(raw_path) + 1,
                    kind="property",
                    eager=False,
                )
            )

    for ext_id, line_number in used_ext.items():
        if ext_id not in parsed.ext_resources:
            parsed.diagnostics.append(
                Diagnostic(
                    rule="undefined-ext-resource",
                    severity=Severity.ERROR,
                    message=f"ExtResource({ext_id!r}) is not defined",
                    file=rel,
                    line=line_number,
                    anchor=ext_id,
                )
            )
    for sub_id, line_number in used_sub.items():
        if sub_id not in parsed.sub_resource_ids:
            parsed.diagnostics.append(
                Diagnostic(
                    rule="undefined-sub-resource",
                    severity=Severity.ERROR,
                    message=f"SubResource({sub_id!r}) is not defined",
                    file=rel,
                    line=line_number,
                    anchor=sub_id,
                )
            )
    return parsed


def _metadata_lines(text: str) -> Iterator[tuple[int, str]]:
    quote: str | None = None
    escaped = False
    for line_number, raw_line in enumerate(text.splitlines(keepends=True), 1):
        continuation = quote is not None
        for index, char in enumerate(raw_line):
            if escaped:
                escaped = False
            elif quote is not None:
                if char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char == ";":
                raw_line = raw_line[:index]
                break
            elif char in ('"', "'"):
                quote = char
        if not continuation:
            yield line_number, raw_line


def parse_uid_file(project: Project, rel: str) -> ParsedFile:
    parsed = ParsedFile(path=rel, kind="uid" if rel.endswith(".uid") else "import")
    try:
        text = project.files[rel].read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        parsed.diagnostics.append(
            Diagnostic(
                rule="parse-error",
                severity=Severity.ERROR,
                message=f"cannot read file: {exc}",
                file=rel,
                anchor="read-error",
            )
        )
        return parsed

    if parsed.kind == "uid":
        lines = text.splitlines()
        if lines:
            parsed.uid = lines[0].strip() or None
            if parsed.uid:
                parsed.uid_line = 1
                parsed.uid_column = lines[0].find(parsed.uid) + 1
        return parsed

    section = ""
    for line_number, raw_line in _metadata_lines(text):
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
        elif section == "remap" and line.partition("=")[0].strip() == "uid":
            parsed.uid = _parse_attrs(line).get("uid")
            if parsed.uid:
                parsed.uid_line = line_number
                parsed.uid_column = raw_line.find(parsed.uid) + 1
    return parsed


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _position(starts: list[int], offset: int) -> tuple[int, int]:
    index = bisect.bisect_right(starts, offset) - 1
    return index + 1, offset - starts[index] + 1


def _tokenize_gd(text: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
            continue
        if char == "#":
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
            continue
        if char in ('"', "'"):
            quote = char
            if text.startswith(quote * 3, index):
                start = index
                body_start = index + 3
                end = text.find(quote * 3, body_start)
                if end < 0:
                    tokens.append(Token("string", text[body_start:], start, length))
                    index = length
                else:
                    tokens.append(
                        Token("string", text[body_start:end], start, end + 3)
                    )
                    index = end + 3
                continue
            start = index
            cursor = index + 1
            chunks: list[str] = []
            while cursor < length:
                current = text[cursor]
                if current == "\\" and cursor + 1 < length:
                    chunks.append(text[cursor : cursor + 2])
                    cursor += 2
                    continue
                if current == quote:
                    cursor += 1
                    break
                if current == "\n":
                    break
                chunks.append(current)
                cursor += 1
            tokens.append(Token("string", "".join(chunks), start, cursor))
            index = cursor
            continue
        if char.isalpha() or char == "_":
            cursor = index + 1
            while cursor < length and (
                text[cursor].isalnum() or text[cursor] == "_"
            ):
                cursor += 1
            tokens.append(Token("ident", text[index:cursor], index, cursor))
            index = cursor
            continue
        tokens.append(Token("punct", char, index, index + 1))
        index += 1
    return tokens


def parse_gdscript_file(project: Project, rel: str) -> ParsedFile:
    parsed = ParsedFile(path=rel, kind="script")
    try:
        text = project.files[rel].read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        parsed.diagnostics.append(
            Diagnostic(
                rule="parse-error",
                severity=Severity.ERROR,
                message=f"cannot read file: {exc}",
                file=rel,
                anchor="read-error",
            )
        )
        return parsed

    tokens = _tokenize_gd(text)
    starts = _line_starts(text)
    for index, token in enumerate(tokens):
        if token.kind != "ident":
            continue
        if token.value == "change_scene_to_file":
            receiver = [
                (item.kind, item.value)
                for item in tokens[max(0, index - 4):index]
            ]
            if receiver != [
                ("ident", "get_tree"), ("punct", "("), ("punct", ")"), ("punct", ".")
            ]:
                continue
        elif token.value in ("preload", "load"):
            if index > 0:
                previous = tokens[index - 1]
                if previous.kind == "punct" and previous.value in (".", ":"):
                    continue
        else:
            continue
        if index + 3 >= len(tokens):
            continue
        opening = tokens[index + 1]
        if opening.kind != "punct" or opening.value != "(":
            continue
        cursor = index + 2
        argument = tokens[cursor]
        # Godot raw strings (r"...") tokenize as an identifier plus a string.
        if argument.kind == "ident" and argument.value == "r":
            cursor += 1
            if cursor >= len(tokens):
                continue
            argument = tokens[cursor]
        if argument.kind != "string" or not argument.value.startswith("res://"):
            continue
        if cursor + 1 >= len(tokens):
            continue
        closing = tokens[cursor + 1]
        if closing.kind != "punct" or closing.value != ")":
            continue
        line, column = _position(starts, token.start)
        eager = token.value == "preload"
        parsed.refs.append(
            Reference(
                raw=argument.value,
                file=rel,
                line=line,
                column=column,
                kind=token.value,
                eager=eager,
            )
        )
        if eager:
            parsed.eager_edges.add(argument.value)
    return parsed
