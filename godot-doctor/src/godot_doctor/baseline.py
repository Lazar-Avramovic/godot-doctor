from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Sequence

from .formats import (
    RULE_ALIASES,
    SCHEMA_VERSION,
    TOOL,
    fingerprint,
    message_fingerprint,
)
from .model import Diagnostic, Severity
from .rules import AnalysisResult


class BaselineError(Exception):
    """Raised when a baseline report cannot be read or understood."""


def _severity(value: object, index: int) -> Severity:
    try:
        return Severity(str(value))
    except ValueError as exc:
        raise BaselineError(
            f"baseline diagnostic {index} has an unknown severity: {value!r}"
        ) from exc


def _integer(value: object) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def load_baseline(path: str) -> tuple[dict, list[Diagnostic]]:
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BaselineError("a baseline must be a JSON report object")
    version = payload.get("schema_version")
    if version is not None and version != SCHEMA_VERSION:
        raise BaselineError(
            f"baseline schema version {version!r} is not supported "
            f"(expected {SCHEMA_VERSION})"
        )
    rows = payload.get("diagnostics")
    if not isinstance(rows, list):
        raise BaselineError("baseline has no diagnostics list")
    diagnostics: list[Diagnostic] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BaselineError(f"baseline diagnostic {index} is not an object")
        rule = row.get("rule")
        severity = row.get("severity")
        message = row.get("message")
        file = row.get("file")
        if not all(isinstance(value, str) and value for value in (rule, severity, message, file)):
            raise BaselineError(
                f"baseline diagnostic {index} needs rule, severity, message and file strings"
            )
        raw_related = row.get("related")
        if raw_related is None:
            related: tuple[str, ...] = ()
        elif isinstance(raw_related, list) and all(
            isinstance(value, str) for value in raw_related
        ):
            related = tuple(raw_related)
        else:
            raise BaselineError(f"baseline diagnostic {index} has malformed related paths")
        diagnostics.append(
            Diagnostic(
                rule=rule,
                severity=_severity(severity, index),
                message=message,
                file=file,
                line=_integer(row.get("line")),
                column=_integer(row.get("column")),
                related=related,
                anchor=row.get("anchor") if isinstance(row.get("anchor"), str) else "",
            )
        )
    return payload, diagnostics


def _counts(rows: Sequence[Diagnostic]) -> dict[str, int]:
    counts = Counter(item.severity for item in rows)
    return {
        "errors": counts[Severity.ERROR],
        "warnings": counts[Severity.WARNING],
        "infos": counts[Severity.INFO],
        "total": len(rows),
    }


def _line_key(item: Diagnostic) -> tuple[str, str, int] | None:
    if item.line is None:
        return None
    return (RULE_ALIASES.get(item.rule, item.rule), item.file, item.line)


def _match(
    current: Sequence[Diagnostic], baseline: Sequence[Diagnostic]
) -> tuple[set[int], set[int]]:
    matched_current: set[int] = set()
    matched_baseline: set[int] = set()

    def apply(key, *, legacy_only: bool = False, skip_none: bool = False) -> None:
        buckets: dict[object, list[int]] = defaultdict(list)
        for index, row in enumerate(baseline):
            if index in matched_baseline or (legacy_only and row.anchor):
                continue
            value = key(row)
            if skip_none and value is None:
                continue
            buckets[value].append(index)
        for index, row in enumerate(current):
            if index in matched_current:
                continue
            value = key(row)
            if skip_none and value is None:
                continue
            bucket = buckets.get(value)
            if bucket:
                matched_current.add(index)
                matched_baseline.add(bucket.pop())

    apply(fingerprint)
    # Older reports have no anchor: match their message fingerprints, then, when a
    # message was reworded, the same rule/file/line on an unchanged tree.
    apply(message_fingerprint, legacy_only=True)
    apply(_line_key, legacy_only=True, skip_none=True)
    return matched_current, matched_baseline


def diff_report(
    result: AnalysisResult,
    baseline_path: str,
    baseline_payload: dict,
    baseline_rows: Sequence[Diagnostic],
    *,
    hidden: int = 0,
    baseline_hidden: int = 0,
) -> tuple[dict[str, object], list[Diagnostic], list[Diagnostic]]:
    current = list(result.diagnostics)
    matched_current, matched_baseline = _match(current, baseline_rows)
    new_rows = [row for index, row in enumerate(current) if index not in matched_current]
    resolved_rows = [
        row for index, row in enumerate(baseline_rows) if index not in matched_baseline
    ]
    persisting = len(matched_current)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": dict(TOOL),
        "project": str(result.project.root),
        "baseline": {
            "path": str(Path(baseline_path).expanduser()),
            "project": baseline_payload.get("project"),
            "summary": baseline_payload.get("summary"),
        },
        "summary": {
            "new": _counts(new_rows),
            "resolved": _counts(resolved_rows),
            "persisting": persisting,
            "hidden": hidden,
            "baseline_hidden": baseline_hidden,
        },
        "new": [
            {**item.to_dict(), "fingerprint": fingerprint(item)} for item in new_rows
        ],
        "resolved": [
            {**item.to_dict(), "fingerprint": fingerprint(item)}
            for item in resolved_rows
        ],
    }
    return payload, new_rows, resolved_rows


def render_diff(payload: dict) -> str:
    def row_location(row: dict) -> str:
        value = row["file"]
        if row.get("line") is not None:
            value += f":{row['line']}"
            if row.get("column") is not None:
                value += f":{row['column']}"
        return value

    lines = [
        f"+ {row_location(item)}: {item['severity']}: {item['message']} [{item['rule']}]"
        for item in payload["new"]
    ]
    lines.extend(
        f"- {row_location(item)}: {item['severity']}: {item['message']} [{item['rule']}]"
        for item in payload["resolved"]
    )
    if not lines:
        lines.append("No changes since baseline.")
    counts = payload["summary"]
    lines.append("")
    lines.append(
        f"{counts['new']['total']} new, {counts['resolved']['total']} resolved, "
        f"{counts['persisting']} persisting."
    )
    if counts.get("hidden") or counts.get("baseline_hidden"):
        lines.append(
            f"{counts.get('hidden', 0)} current and "
            f"{counts.get('baseline_hidden', 0)} baseline diagnostic(s) hidden by filters."
        )
    return "\n".join(lines)
