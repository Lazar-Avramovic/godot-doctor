from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Sequence

from .formats import fingerprint, SCHEMA_VERSION, TOOL
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
    except OSError as exc:
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
    baseline_counts = Counter(fingerprint(item) for item in baseline_rows)
    current_counts = Counter(fingerprint(item) for item in current)

    new_quota = current_counts - baseline_counts
    resolved_quota = baseline_counts - current_counts

    new_rows: list[Diagnostic] = []
    for item in current:
        key = fingerprint(item)
        if new_quota[key] > 0:
            new_quota[key] -= 1
            new_rows.append(item)

    resolved_rows: list[Diagnostic] = []
    for item in baseline_rows:
        key = fingerprint(item)
        if resolved_quota[key] > 0:
            resolved_quota[key] -= 1
            resolved_rows.append(item)

    persisting = sum(
        min(current_counts[key], baseline_counts[key])
        for key in current_counts.keys() | baseline_counts.keys()
    )
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
