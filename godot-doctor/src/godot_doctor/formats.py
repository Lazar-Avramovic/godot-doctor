from __future__ import annotations

import fnmatch
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence
from urllib.parse import quote

from . import __version__
from .model import Diagnostic, Severity
from .rulebook import get_rule

if TYPE_CHECKING:
    from .rules import AnalysisResult

SCHEMA_VERSION = 2
TOOL = {"name": "godot-doctor", "version": __version__}
SCAN_FORMATS = ("text", "json", "ndjson", "sarif", "github")
SEVERITY_NAMES = ("info", "warning", "error")
SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}
SARIF_LEVEL = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "note"}
GITHUB_LEVEL = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "notice"}
# A rule rename must not invalidate baselines written under the old id.
RULE_ALIASES = {"config-parse-error": "config-nonkey-line"}


def parse_severity(value: str) -> Severity:
    return {
        "error": Severity.ERROR,
        "warning": Severity.WARNING,
        "info": Severity.INFO,
    }[value]


def fingerprint_value(rule: str, file: str, subject: str) -> str:
    canonical = RULE_ALIASES.get(rule, rule)
    digest = hashlib.sha256(
        f"{canonical}\0{file}\0{subject}".encode("utf-8", "surrogatepass")
    ).hexdigest()[:12]
    return f"{rule}:{digest}"


def fingerprint(diagnostic: Diagnostic) -> str:
    return fingerprint_value(
        diagnostic.rule,
        diagnostic.file,
        diagnostic.anchor or diagnostic.message,
    )


def message_fingerprint(diagnostic: Diagnostic) -> str:
    """The pre-anchor scheme, used only to match legacy baselines."""
    return fingerprint_value(diagnostic.rule, diagnostic.file, diagnostic.message)


def fingerprints(diagnostics: Iterable[Diagnostic]) -> list[str]:
    return [fingerprint(item) for item in diagnostics]


def filter_diagnostics(
    diagnostics: Iterable[Diagnostic],
    *,
    rules: Sequence[str] | None = None,
    ignore_rules: Sequence[str] | None = None,
    severities: Sequence[str] | None = None,
    search: str | None = None,
    file_patterns: Sequence[str] | None = None,
) -> list[Diagnostic]:
    selected = set(rules) if rules else None
    ignored = set(ignore_rules) if ignore_rules else None
    wanted = set(severities) if severities else None
    needle = search.strip().casefold() if search else None
    result: list[Diagnostic] = []
    for item in diagnostics:
        if selected is not None and item.rule not in selected:
            continue
        if ignored is not None and item.rule in ignored:
            continue
        if wanted is not None and item.severity.value not in wanted:
            continue
        if needle is not None:
            haystack = f"{item.message}\n{item.file}\n{item.rule}".casefold()
            if needle not in haystack:
                continue
        if file_patterns and not any(
            fnmatch.fnmatchcase(item.file, pattern) for pattern in file_patterns
        ):
            continue
        result.append(item)
    return result


def summary_counts(
    result: "AnalysisResult", diagnostics: Sequence[Diagnostic], hidden: int = 0
) -> dict[str, int]:
    counts = Counter(item.severity for item in diagnostics)
    return {
        "indexed": result.files_indexed,
        "analyzed": result.files_analyzed,
        "errors": counts[Severity.ERROR],
        "warnings": counts[Severity.WARNING],
        "infos": counts[Severity.INFO],
        "total": len(diagnostics) + hidden,
        "hidden": hidden,
    }


def diagnostic_payload(item: Diagnostic, item_fingerprint: str | None = None) -> dict:
    payload = item.to_dict()
    payload["fingerprint"] = item_fingerprint or fingerprint(item)
    return payload


def report_payload(
    result: "AnalysisResult",
    diagnostics: Sequence[Diagnostic] | None = None,
    hidden: int = 0,
) -> dict[str, object]:
    shown = list(result.diagnostics if diagnostics is None else diagnostics)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": dict(TOOL),
        "project": str(result.project.root),
        "summary": summary_counts(result, shown, hidden),
        "diagnostics": [diagnostic_payload(item) for item in shown],
    }


def format_json_report(
    result: "AnalysisResult",
    diagnostics: Sequence[Diagnostic] | None = None,
    hidden: int = 0,
) -> str:
    return json.dumps(
        report_payload(result, diagnostics, hidden), indent=2, ensure_ascii=True
    )


def location(item: Diagnostic) -> str:
    value = item.file
    if item.line is not None:
        value += f":{item.line}"
        if item.column is not None:
            value += f":{item.column}"
    return value


def summary_line(
    result: "AnalysisResult", diagnostics: Sequence[Diagnostic], hidden: int = 0
) -> str:
    counts = summary_counts(result, diagnostics, hidden)
    text = (
        f"{counts['indexed']} files indexed, {counts['analyzed']} parsed; "
        f"{counts['errors']} error(s), {counts['warnings']} warning(s), "
        f"{counts['infos']} info(s)."
    )
    if hidden:
        text += f" {hidden} diagnostic(s) hidden by filters."
    return text


def format_text_report(
    result: "AnalysisResult",
    diagnostics: Sequence[Diagnostic] | None = None,
    hidden: int = 0,
) -> str:
    shown = list(result.diagnostics if diagnostics is None else diagnostics)
    lines = [
        f"{location(item)}: {item.severity.value}: {item.message} [{item.rule}]"
        for item in shown
    ]
    if not lines:
        lines.append("No issues found." if not hidden else "No issues match these filters.")
    lines.append("")
    lines.append(summary_line(result, shown, hidden))
    return "\n".join(lines)


def _group_key(item: Diagnostic, group_by: str) -> str:
    if group_by == "rule":
        return item.rule
    if group_by == "file":
        return item.file
    if group_by == "directory":
        parts = item.file.split("/")
        return parts[0] if len(parts) > 1 else "."
    raise ValueError(group_by)


def format_grouped_text(
    result: "AnalysisResult",
    diagnostics: Sequence[Diagnostic] | None,
    hidden: int,
    group_by: str,
) -> str:
    shown = list(result.diagnostics if diagnostics is None else diagnostics)
    if not shown:
        lines = ["No issues found." if not hidden else "No issues match these filters."]
    else:
        groups: dict[str, list[Diagnostic]] = {}
        for item in shown:
            groups.setdefault(_group_key(item, group_by), []).append(item)

        def order(key: str) -> tuple[int, str]:
            if group_by == "rule":
                severity = max(
                    (
                        SEVERITY_RANK[item.severity]
                        for item in groups[key]
                    ),
                    default=0,
                )
                return (-severity, key)
            return (0, key)

        lines = []
        for key in sorted(groups, key=order):
            rows = groups[key]
            lines.append(f"{key} ({len(rows)})")
            for item in rows:
                row_location = location(item)
                if group_by == "rule":
                    lines.append(
                        f"  {row_location}: {item.severity.value}: {item.message}"
                    )
                elif group_by == "file":
                    detail = row_location[len(item.file) + 1 :]
                    if detail:
                        detail += " "
                    lines.append(
                        f"  {detail}{item.severity.value}: {item.message} [{item.rule}]"
                    )
                else:
                    prefix = f"{key}/"
                    if key != "." and item.file.startswith(prefix):
                        row_location = row_location[len(prefix) :]
                    lines.append(
                        f"  {row_location}: {item.severity.value}: {item.message} [{item.rule}]"
                    )
    lines.append("")
    lines.append(summary_line(result, shown, hidden))
    return "\n".join(lines)


def format_ndjson(
    result: "AnalysisResult",
    diagnostics: Sequence[Diagnostic] | None,
    hidden: int = 0,
) -> str:
    shown = list(result.diagnostics if diagnostics is None else diagnostics)
    rows: list[dict] = [
        {
            "type": "meta",
            "schema_version": SCHEMA_VERSION,
            "tool": dict(TOOL),
            "project": str(result.project.root),
        }
    ]
    for item in shown:
        row = diagnostic_payload(item)
        row["type"] = "diagnostic"
        rows.append(row)
    rows.append({"type": "summary", "summary": summary_counts(result, shown, hidden)})
    return "\n".join(json.dumps(row, ensure_ascii=True) for row in rows)


def _original_uri_base(root: Path) -> str:
    try:
        return root.resolve().as_uri().rstrip("/") + "/"
    except (OSError, ValueError):
        return ""


def _sarif_uri(file: str) -> str:
    return quote(file, safe="/")


def format_sarif(
    result: "AnalysisResult", diagnostics: Sequence[Diagnostic] | None, hidden: int = 0
) -> str:
    shown = list(result.diagnostics if diagnostics is None else diagnostics)
    used_rules: dict[str, dict] = {}
    for item in shown:
        meta = get_rule(item.rule)
        used_rules[item.rule] = {
            "id": item.rule,
            "name": item.rule,
            "shortDescription": {"text": meta.title if meta else item.rule},
            "fullDescription": {
                "text": (
                    f"{meta.description} Fix: {meta.remediation}"
                    if meta
                    else item.rule
                )
            },
            "defaultConfiguration": {
                "level": SARIF_LEVEL[parse_severity(meta.severity)] if meta else "warning"
            },
            "properties": {
                "category": meta.category if meta else "",
                "severity": meta.severity if meta else "",
            },
        }
    rule_ids = sorted(used_rules)
    rule_index = {rule_id: index for index, rule_id in enumerate(rule_ids)}

    results = []
    for item in shown:
        region: dict[str, int] = {}
        if item.line is not None:
            region["startLine"] = item.line
            if item.column is not None:
                region["startColumn"] = item.column
        physical: dict[str, object] = {
            "artifactLocation": {"uri": _sarif_uri(item.file), "uriBaseId": "%SRCROOT%"}
        }
        if region:
            physical["region"] = region
        row: dict[str, object] = {
            "ruleId": item.rule,
            "ruleIndex": rule_index[item.rule],
            "level": SARIF_LEVEL[item.severity],
            "message": {"text": item.message},
            "locations": [{"physicalLocation": physical}],
            "partialFingerprints": {"godotDoctor/v1": fingerprint(item)},
            "properties": {"rule": item.rule, "severity": item.severity.value},
        }
        related = [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": _sarif_uri(name), "uriBaseId": "%SRCROOT%"}
                }
            }
            for name in item.related
        ]
        if related:
            row["relatedLocations"] = related
        results.append(row)

    run: dict[str, object] = {
        "tool": {
            "driver": {
                "name": TOOL["name"],
                "version": TOOL["version"],
                "rules": [used_rules[rule_id] for rule_id in rule_ids],
            }
        },
        "columnKind": "unicodeCodePoints",
        "originalUriBaseIds": {"%SRCROOT%": {"uri": _original_uri_base(result.project.root)}},
        "results": results,
        "invocations": [{"executionSuccessful": True}],
    }
    if hidden:
        run["properties"] = {"hiddenByFilters": hidden}
    document = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }
    return json.dumps(document, indent=2, ensure_ascii=True)


def _github_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _github_property(value: str) -> str:
    return _github_escape(value).replace(":", "%3A").replace(",", "%2C")


def _annotation_path(result: "AnalysisResult", file: str) -> str:
    root = result.project.root.resolve()
    try:
        prefix = root.relative_to(Path.cwd().resolve()).as_posix()
    except (OSError, ValueError):
        return file
    return file if prefix == "." else f"{prefix}/{file}"


def format_github(
    result: "AnalysisResult",
    diagnostics: Sequence[Diagnostic] | None,
    hidden: int = 0,
) -> str:
    shown = list(result.diagnostics if diagnostics is None else diagnostics)
    lines = []
    for item in shown:
        properties = [
            f"file={_github_property(_annotation_path(result, item.file))}",
        ]
        if item.line is not None:
            properties.append(f"line={item.line}")
            if item.column is not None:
                properties.append(f"col={item.column}")
        properties.append(f"title={_github_property(item.rule)}")
        lines.append(
            f"::{GITHUB_LEVEL[item.severity]} {','.join(properties)}::"
            f"{_github_escape(item.message)}"
        )
    lines.append(
        "::notice title=godot-doctor::"
        f"{_github_escape(summary_line(result, shown, hidden))}"
    )
    return "\n".join(lines)


def render_scan(
    fmt: str,
    result: "AnalysisResult",
    diagnostics: Sequence[Diagnostic],
    hidden: int,
    group_by: str = "none",
) -> str:
    if fmt == "json":
        return format_json_report(result, diagnostics, hidden)
    if fmt == "ndjson":
        return format_ndjson(result, diagnostics, hidden)
    if fmt == "sarif":
        return format_sarif(result, diagnostics, hidden)
    if fmt == "github":
        return format_github(result, diagnostics, hidden)
    if group_by != "none":
        return format_grouped_text(result, diagnostics, hidden, group_by)
    return format_text_report(result, diagnostics, hidden)
