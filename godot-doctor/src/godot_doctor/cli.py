from __future__ import annotations

import argparse
import re
import sys
import traceback
from dataclasses import replace
from pathlib import Path

from . import __version__
from .baseline import BaselineError, diff_report, load_baseline, render_diff
from .formats import (
    SCAN_FORMATS,
    SCHEMA_VERSION,
    SEVERITY_NAMES,
    TOOL,
    filter_diagnostics,
    parse_severity,
    render_scan,
)
from .model import Severity
from .inspect import (
    REFERENCE_KINDS,
    InspectError,
    browse_directory,
    discover_projects,
    dumps,
    graph_payload,
    references_payload,
    render_browse,
    render_discover,
    render_excerpt,
    render_graph_dot,
    render_graph_text,
    render_references,
    render_stats,
    source_excerpt,
    stats_payload,
)
from .project import Project, ProjectError
from .rulebook import RULEBOOK, get_rule, list_rules
from .rules import analyze, analyze_parsed, parse_all

COMMAND_SUMMARIES = {
    "scan": "Analyze a project (default when no command is given).",
    "rules": "List diagnostic rules with severity and summary.",
    "explain": "Explain one diagnostic rule and how to fix it.",
    "context": "Print a source excerpt around a file location.",
    "refs": "List resource references and how each one resolves.",
    "graph": "Show the eager dependency graph and its cycles.",
    "stats": "Summarize files, diagnostics, references and UIDs.",
    "discover": "Find Godot projects under a directory.",
    "browse": "List subdirectories and mark which are Godot projects.",
    "diff": "Compare a scan with a saved JSON report.",
    "describe": "Print a machine-readable description of this CLI and its report schema.",
}
COMMANDS = tuple(COMMAND_SUMMARIES)
JSON_KEYS = {
    "scan": ("schema_version", "tool", "project", "summary", "diagnostics"),
    "rules": ("schema_version", "tool", "count", "rules"),
    "explain": ("schema_version", "tool", "rule"),
    "context": (
        "schema_version", "tool", "project", "file", "line", "start_line",
        "end_line", "lines", "has_more_before", "has_more_after",
    ),
    "refs": ("schema_version", "tool", "project", "summary", "references"),
    "graph": ("schema_version", "tool", "project", "summary", "nodes", "edges", "cycles"),
    "stats": ("schema_version", "tool", "project", "files", "diagnostics", "references", "uids"),
    "discover": (
        "schema_version", "tool", "root", "projects", "count",
        "unreadable_count", "truncated", "depth",
    ),
    "browse": (
        "schema_version", "tool", "path", "parent", "is_project",
        "directories", "unreadable_count",
    ),
    "diff": ("schema_version", "tool", "project", "baseline", "summary", "new", "resolved"),
    "describe": (
        "schema_version", "tool", "usage", "conventions", "exit_codes",
        "error_codes", "formats", "commands", "report_schema", "diff",
    ),
}

REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "godot-doctor scan report",
    "type": "object",
    "required": ["schema_version", "tool", "project", "summary", "diagnostics"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "tool": {
            "type": "object",
            "required": ["name", "version"],
            "properties": {
                "name": {"const": "godot-doctor"},
                "version": {"type": "string"},
            },
        },
        "project": {
            "type": "string",
            "description": "Absolute project root. Diagnostic paths are relative to it.",
        },
        "summary": {
            "type": "object",
            "required": [
                "indexed", "analyzed", "errors", "warnings", "infos", "total", "hidden",
            ],
            "properties": {
                "indexed": {"type": "integer"},
                "analyzed": {"type": "integer"},
                "errors": {"type": "integer"},
                "warnings": {"type": "integer"},
                "infos": {"type": "integer"},
                "total": {"type": "integer", "description": "Shown plus hidden."},
                "hidden": {"type": "integer", "description": "Removed by filters."},
            },
        },
        "diagnostics": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rule", "severity", "message", "file", "fingerprint"],
                "properties": {
                    "rule": {"type": "string"},
                    "severity": {"enum": ["error", "warning", "info"]},
                    "message": {"type": "string"},
                    "file": {
                        "type": "string",
                        "description": "POSIX path relative to project.",
                    },
                    "line": {"type": "integer"},
                    "column": {"type": "integer"},
                    "related": {"type": "array", "items": {"type": "string"}},
                    "fingerprint": {
                        "type": "string",
                        "description": (
                            "Stable rule:digest of rule, file and message. Line and "
                            "column changes do not alter it; equal diagnostics share it."
                        ),
                    },
                },
            },
        },
    },
}


class CliError(Exception):
    def __init__(self, message: str, code: str = "cli-error"):
        super().__init__(message)
        self.code = code


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="backslashreplace")
            except (OSError, TypeError, ValueError):
                pass


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="write the report to FILE instead of standard output ('-' means stdout)",
    )


def _add_json(parser: argparse.ArgumentParser, help_text: str = "emit JSON") -> None:
    parser.add_argument("--json", dest="json_flag", action="store_true", help=help_text)


def _add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--severity",
        action="append",
        default=None,
        metavar="SEVERITY",
        help=(
            "show only this severity (info, warning or error); repeat or "
            "comma-separate for several (default: all)"
        ),
    )
    parser.add_argument(
        "--rule",
        action="append",
        default=[],
        metavar="RULE",
        help="show only this rule; repeat or comma-separate for several",
    )
    parser.add_argument(
        "--ignore-rule",
        action="append",
        default=[],
        metavar="RULE",
        help="hide this rule; repeat or comma-separate for several",
    )
    parser.add_argument(
        "--search",
        metavar="TEXT",
        default=None,
        help=(
            "show only diagnostics whose message, project-relative file or rule "
            "contains TEXT (case-insensitive)"
        ),
    )
    parser.add_argument(
        "--file",
        dest="file_patterns",
        action="append",
        default=[],
        metavar="GLOB",
        help="show only diagnostics whose project-relative file matches GLOB; repeatable",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="godot-doctor",
        description=(
            "Static analysis for Godot projects. With no command, analyzes PATH "
            "(default: the current directory)."
        ),
        epilog=(
            "Checks resource references, eager dependency cycles, and duplicate Godot 4 UIDs. "
            "UID sources: .tscn/.tres headers, .gd/.gdshader/.gdshaderinc .uid sidecars "
            "(Godot 4.4+), and adjacent .import metadata. Binary resources, custom "
            "loaders, and generated caches are not inspected. Project files are never modified. "
            "Run 'godot-doctor describe' for the machine-readable command and report contract."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command", title="commands")

    scan = subparsers.add_parser(
        "scan",
        help=COMMAND_SUMMARIES["scan"],
        description="Analyze a Godot project and report diagnostics.",
    )
    scan.add_argument(
        "path", nargs="?", default=".", help="project directory or a file inside it (default: .)"
    )
    scan.add_argument(
        "--format",
        choices=SCAN_FORMATS,
        default=None,
        help="output format (default: text)",
    )
    _add_json(scan, "alias for --format json")
    scan.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on warnings as well as errors (same as --fail-on warning)",
    )
    scan.add_argument(
        "--fail-on",
        choices=("error", "warning", "info", "never"),
        default=None,
        help="exit 1 at or above this severity (default: error)",
    )
    _add_filter_arguments(scan)
    scan.add_argument(
        "--group-by",
        choices=("none", "rule", "file", "directory"),
        default="none",
        help="group text output (default: none)",
    )
    _add_output(scan)
    scan.add_argument(
        "--quiet", action="store_true", help="print nothing; use the exit code only"
    )
    scan.add_argument("--gui", action="store_true", help="open the local graphical interface")
    scan.add_argument(
        "--port", type=int, default=None, help="GUI port (default: choose a free port)"
    )
    scan.add_argument(
        "--no-browser", action="store_true", help="start the GUI without opening a browser"
    )
    scan.add_argument(
        "--exit-on-stdin-close",
        action="store_true",
        help="stop the GUI when standard input closes (used by the desktop app)",
    )
    scan.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    rules = subparsers.add_parser(
        "rules",
        help=COMMAND_SUMMARIES["rules"],
        description="List every diagnostic rule with its default severity.",
    )
    _add_json(rules)
    _add_output(rules)

    explain = subparsers.add_parser(
        "explain",
        help=COMMAND_SUMMARIES["explain"],
        description="Explain one diagnostic rule: cause, consequences and remediation.",
    )
    explain.add_argument("rule", metavar="RULE", help="rule id, for example broken-reference")
    _add_json(explain)
    _add_output(explain)

    context = subparsers.add_parser(
        "context",
        help=COMMAND_SUMMARIES["context"],
        description=(
            "Print a source excerpt around FILE:LINE. The file must be indexed inside the "
            "selected project; paths outside the project root are refused."
        ),
    )
    context.add_argument(
        "file", metavar="FILE", help="project-relative path, optionally suffixed with :LINE"
    )
    context.add_argument("--line", type=int, default=None, help="1-based source line")
    context.add_argument(
        "--radius", type=int, default=7, help="lines before and after the target (default: 7)"
    )
    context.add_argument(
        "--path", default=None, help="project directory (default: inferred from FILE or .)"
    )
    _add_json(context)
    _add_output(context)

    refs = subparsers.add_parser(
        "refs",
        help=COMMAND_SUMMARIES["refs"],
        description="List every res:// reference and whether it resolves.",
    )
    refs.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
    refs.add_argument(
        "--unresolved", action="store_true", help="show only references with a non-ok status"
    )
    refs.add_argument("--eager", action="store_true", help="show only eager references")
    refs.add_argument(
        "--kind",
        action="append",
        default=[],
        metavar="KIND",
        help=(
            "show only this kind (project, ext_resource, property, preload, load, "
            "change_scene_to_file); repeatable"
        ),
    )
    _add_json(refs)
    _add_output(refs)

    graph = subparsers.add_parser(
        "graph",
        help=COMMAND_SUMMARIES["graph"],
        description=(
            "Show the eager dependency graph built from ext_resource and preload() edges. "
            "Lazy load() references are not edges."
        ),
    )
    graph.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
    graph.add_argument(
        "--format",
        dest="graph_format",
        choices=("text", "json", "dot"),
        default=None,
        help="output format (default: text)",
    )
    _add_json(graph, "alias for --format json")
    _add_output(graph)

    stats = subparsers.add_parser(
        "stats",
        help=COMMAND_SUMMARIES["stats"],
        description="Summarize files, diagnostics, references and UID declarations.",
    )
    stats.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
    stats.add_argument(
        "--top",
        type=int,
        default=10,
        help="number of files with most diagnostics to list (default: 10)",
    )
    _add_json(stats)
    _add_output(stats)

    discover = subparsers.add_parser(
        "discover",
        help=COMMAND_SUMMARIES["discover"],
        description=(
            "Find directories containing project.godot. Hidden and generated/build "
            "directories are skipped."
        ),
    )
    discover.add_argument(
        "path", nargs="?", default=".", help="directory to search (default: .)"
    )
    discover.add_argument(
        "--depth",
        type=int,
        default=None,
        help="descend at most this many levels below the search root",
    )
    discover.add_argument(
        "--limit", type=int, default=None, help="stop after this many projects"
    )
    _add_json(discover)
    _add_output(discover)

    browse = subparsers.add_parser(
        "browse",
        help=COMMAND_SUMMARIES["browse"],
        description="List the subdirectories of a directory and mark Godot projects.",
    )
    browse.add_argument("path", nargs="?", default=".", help="directory (default: .)")
    _add_json(browse)
    _add_output(browse)

    diff = subparsers.add_parser(
        "diff",
        help=COMMAND_SUMMARIES["diff"],
        description=(
            "Scan the project and compare it with a JSON report made earlier. "
            "Fingerprints ignore line and column drift. Filters apply to both the "
            "current scan and the baseline."
        ),
    )
    diff.add_argument("baseline", metavar="BASELINE", help="JSON report from an earlier scan")
    diff.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
    _add_json(diff)
    _add_filter_arguments(diff)
    diff.add_argument(
        "--strict", action="store_true", help="treat new warnings as failing too"
    )
    diff.add_argument(
        "--fail-on",
        choices=("new", "never"),
        default="new",
        help="exit 1 on new errors (default) or never fail",
    )
    _add_output(diff)

    describe = subparsers.add_parser(
        "describe",
        help=COMMAND_SUMMARIES["describe"],
        description="Emit a machine-readable description of commands, formats and schemas.",
    )
    _add_output(describe)
    return parser


def _subparser_choices(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if action.dest == "command" and getattr(action, "choices", None):
            return dict(action.choices)
    return {}


def _help(argv: list[str]) -> int:
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    choices = _subparser_choices(parser)
    name = argv[0]
    if name not in choices:
        print(f"godot-doctor: unknown command {name!r}", file=sys.stderr)
        return 2
    choices[name].print_help()
    return 0


def _split(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part and part not in items:
                items.append(part)
    return items


def _scan_format(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    fmt = args.format
    if args.json_flag:
        if fmt not in (None, "json"):
            parser.error("--json is an alias for --format json; do not combine it with another format")
        fmt = "json"
    return fmt or "text"


def _wants_structured_errors(args: argparse.Namespace) -> bool:
    if args.command == "scan":
        return bool(args.json_flag) or args.format in ("json", "ndjson")
    if args.command == "describe":
        return True
    if args.command == "graph":
        return bool(args.json_flag) or args.graph_format == "json"
    return bool(getattr(args, "json_flag", False))


def _fail(args: argparse.Namespace, code: str, message: str) -> int:
    if _wants_structured_errors(args):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "tool": dict(TOOL),
            "error": {"code": code, "message": message},
        }
        print(dumps(payload), file=sys.stderr)
    else:
        print(f"godot-doctor: {message}", file=sys.stderr)
    return 2


def _emit(text: str, output: str | None) -> None:
    if output and output != "-":
        with Path(output).expanduser().open(
            "w",
            encoding="utf-8",
            errors="backslashreplace",
            newline="\n",
        ) as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
        return
    print(text)


def _project(path: str) -> Project:
    return Project.load(path)


def _rule_choice(value: str, parser: argparse.ArgumentParser) -> str:
    if value in RULEBOOK:
        return value
    import difflib

    suggestion = difflib.get_close_matches(value, list(RULEBOOK), n=1, cutoff=0.6)
    suffix = f"; did you mean {suggestion[0]!r}?" if suggestion else ""
    parser.error(f"unknown rule {value!r}{suffix}")


def _filter_criteria(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    rules = _split(args.rule)
    ignore_rules = _split(args.ignore_rule)
    for rule in (*rules, *ignore_rules):
        _rule_choice(rule, parser)
    severities = _split(args.severity or [])
    for severity in severities:
        if severity not in SEVERITY_NAMES:
            parser.error(
                f"unknown severity {severity!r}; choose from {', '.join(SEVERITY_NAMES)}"
            )
    return {
        "rules": rules,
        "ignore_rules": ignore_rules,
        "severities": severities,
        "search": args.search,
        "file_patterns": list(args.file_patterns),
    }


def _scan(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.gui:
        cli_only = {
            "--format": args.format is not None,
            "--json": args.json_flag,
            "--strict": args.strict,
            "--fail-on": args.fail_on is not None,
            "--severity": bool(args.severity),
            "--rule": bool(args.rule),
            "--ignore-rule": bool(args.ignore_rule),
            "--search": args.search is not None,
            "--file": bool(args.file_patterns),
            "--group-by": args.group_by != "none",
            "--output": args.output is not None,
            "--quiet": args.quiet,
        }
        used = [name for name, enabled in cli_only.items() if enabled]
        if used:
            parser.error(
                f"{', '.join(used)} are CLI options; omit them when using --gui"
            )
        from .gui import serve_gui

        return serve_gui(
            args.path,
            args.port if args.port is not None else 0,
            not args.no_browser,
            exit_on_stdin_close=args.exit_on_stdin_close,
        )
    if args.port is not None or args.no_browser or args.exit_on_stdin_close:
        parser.error("--port, --no-browser and --exit-on-stdin-close require --gui")

    fmt = _scan_format(args, parser)
    if args.group_by != "none" and fmt != "text":
        parser.error("--group-by is available only with text output")
    if args.strict and args.fail_on is not None:
        parser.error("--strict and --fail-on are mutually exclusive")
    fail_on = "warning" if args.strict else (args.fail_on or "error")
    criteria = _filter_criteria(args, parser)

    result = analyze(_project(args.path))
    shown = filter_diagnostics(result.diagnostics, **criteria)
    hidden = len(result.diagnostics) - len(shown)
    text = render_scan(fmt, result, shown, hidden, args.group_by)
    if not args.quiet or args.output:
        _emit(text, args.output)
    if fail_on == "never" or not shown:
        return 0
    threshold = parse_severity(fail_on)
    return 1 if any(item.severity.rank >= threshold.rank for item in shown) else 0


def _rules(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    rows = list_rules()
    if args.json_flag:
        text = dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "tool": dict(TOOL),
                "count": len(rows),
                "rules": [rule.to_dict() for rule in rows],
            }
        )
    else:
        width = max(len(rule.id) for rule in rows)
        lines = [
            f"{rule.id.ljust(width)}  {rule.severity.ljust(7)}  {rule.title}"
            for rule in rows
        ]
        lines.append("")
        lines.append(f"{len(rows)} rule(s). Use 'godot-doctor explain RULE' for detail.")
        text = "\n".join(lines)
    _emit(text, args.output)
    return 0


def _explain(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    rule = get_rule(args.rule)
    if rule is None:
        import difflib

        suggestion = difflib.get_close_matches(
            args.rule, list(RULEBOOK), n=1, cutoff=0.6
        )
        message = f"unknown rule {args.rule!r}"
        if suggestion:
            message += f"; did you mean {suggestion[0]!r}?"
        raise CliError(message, "unknown-rule")
    if args.json_flag:
        text = dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "tool": dict(TOOL),
                "rule": rule.to_dict(),
            }
        )
    else:
        text = "\n".join(
            [
                f"{rule.id} ({rule.severity})",
                rule.title,
                "",
                f"Category: {rule.category}",
                f"Why: {rule.description}",
                f"Fix: {rule.remediation}",
            ]
        )
    _emit(text, args.output)
    return 0


def _context(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    file = args.file
    line = args.line
    if line is None:
        match = re.match(r"^(.*):(\d+)$", file)
        if match:
            file = match.group(1)
            line = int(match.group(2))
    if line is None:
        line = 1
    source = args.path
    if source is None:
        source = file if Path(file).is_absolute() else "."
    project = _project(source)
    excerpt = source_excerpt(project, file, line, args.radius)
    if args.json_flag:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "tool": dict(TOOL),
            "project": str(project.root),
            **excerpt,
        }
        text = dumps(payload)
    else:
        text = render_excerpt(project, excerpt)
    _emit(text, args.output)
    return 0


def _refs(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    kinds = _split(args.kind)
    for kind in kinds:
        if kind not in REFERENCE_KINDS:
            parser.error(
                f"unknown reference kind {kind!r}; "
                f"choose from {', '.join(REFERENCE_KINDS)}"
            )
    project = _project(args.path)
    parsed = parse_all(project)
    payload = references_payload(
        project,
        parsed,
        unresolved=args.unresolved,
        eager=args.eager,
        kinds=kinds,
    )
    text = dumps(payload) if args.json_flag else render_references(payload)
    _emit(text, args.output)
    return 0


def _graph(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    fmt = args.graph_format
    if args.json_flag:
        if fmt not in (None, "json"):
            parser.error("--json is an alias for --format json; do not combine it with another format")
        fmt = "json"
    fmt = fmt or "text"
    project = _project(args.path)
    payload = graph_payload(project, parse_all(project))
    if fmt == "json":
        text = dumps(payload)
    elif fmt == "dot":
        text = render_graph_dot(payload)
    else:
        text = render_graph_text(payload)
    _emit(text, args.output)
    return 0


def _stats(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not 1 <= args.top <= 1000:
        parser.error("--top must be between 1 and 1000")
    project = _project(args.path)
    parsed = parse_all(project)
    result = analyze_parsed(project, parsed)
    payload = stats_payload(result, parsed, args.top)
    text = dumps(payload) if args.json_flag else render_stats(payload)
    _emit(text, args.output)
    return 0


def _discover(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    payload = discover_projects(args.path, args.depth, args.limit)
    body = {
        "schema_version": SCHEMA_VERSION,
        "tool": dict(TOOL),
        **payload,
    }
    text = dumps(body) if args.json_flag else render_discover(payload)
    _emit(text, args.output)
    return 0


def _browse(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    payload = browse_directory(args.path)
    body = {
        "schema_version": SCHEMA_VERSION,
        "tool": dict(TOOL),
        **payload,
    }
    text = dumps(body) if args.json_flag else render_browse(payload)
    _emit(text, args.output)
    return 0


def _diff(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    criteria = _filter_criteria(args, parser)
    baseline_payload, baseline_rows = load_baseline(args.baseline)
    result = analyze(_project(args.path))
    shown = filter_diagnostics(result.diagnostics, **criteria)
    filtered_baseline = filter_diagnostics(baseline_rows, **criteria)
    hidden = len(result.diagnostics) - len(shown)
    baseline_hidden = len(baseline_rows) - len(filtered_baseline)
    payload, new_rows, _resolved_rows = diff_report(
        replace(result, diagnostics=shown),
        args.baseline,
        baseline_payload,
        filtered_baseline,
        hidden=hidden,
        baseline_hidden=baseline_hidden,
    )
    text = dumps(payload) if args.json_flag else render_diff(payload)
    _emit(text, args.output)
    if args.fail_on == "never":
        return 0
    if any(item.severity.rank >= Severity.ERROR.rank for item in new_rows):
        return 1
    if args.strict and any(item.severity == Severity.WARNING for item in new_rows):
        return 1
    return 0


def _option_entries(parser: argparse.ArgumentParser) -> list[dict]:
    entries: list[dict] = []
    for action in parser._actions:
        if action.dest in ("help", "version", "command"):
            continue
        flags = list(action.option_strings)
        entry: dict[str, object] = {
            "name": flags[0] if flags else action.dest,
            "flags": flags,
            "dest": action.dest,
            "help": action.help or "",
        }
        if action.metavar is not None:
            entry["metavar"] = action.metavar
        elif flags and action.nargs != 0:
            entry["metavar"] = action.dest.upper()
        if getattr(action, "choices", None):
            entry["choices"] = list(action.choices)
        if getattr(action, "required", False):
            entry["required"] = True
        if isinstance(action.default, (str, int, float, bool)) or action.default is None:
            entry["default"] = action.default
        entries.append(entry)
    return entries


def _describe(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    subparsers = _subparser_choices(parser)
    commands = []
    for name in COMMANDS:
        sub = subparsers.get(name)
        if sub is None:
            continue
        commands.append(
            {
                "name": name,
                "summary": COMMAND_SUMMARIES[name],
                "usage": sub.format_usage().strip().removeprefix("usage: "),
                "json_keys": list(JSON_KEYS.get(name, ())),
                "options": _option_entries(sub),
            }
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": dict(TOOL),
        "usage": "godot-doctor [command] [options]",
        "conventions": {
            "paths": (
                "project is absolute; diagnostic and reference file paths are POSIX-relative "
                "to project. Context accepts a project-relative or in-project absolute path."
            ),
            "filters": (
                "scan and diff filters (severity, rule, ignore-rule, search, file) define "
                "both the report and the exit decision; diff applies them to the current "
                "scan and the baseline. summary.total and summary.hidden keep the full "
                "count visible."
            ),
            "fingerprints": (
                "fingerprint is rule plus a digest of rule, file and message. Line and column "
                "changes do not alter it, so diff ignores code moved within a file. Equal "
                "diagnostics share a fingerprint and are compared by count."
            ),
            "determinism": (
                "scan and diff output contain no timestamps; unchanged inputs produce identical "
                "bytes. Rules are ordered by file, line, column, rule and message."
            ),
            "read_only": (
                "the analyzer never modifies project files. --output writes only the "
                "path you name."
            ),
            "errors": (
                "runtime failures with JSON selected emit one object on stderr: "
                "{schema_version, tool, error:{code, message}}, with exit code 2. "
                "Argument-parser errors stay plain text. An unexpected internal error "
                "exits 3 with a traceback instead."
            ),
        },
        "exit_codes": {
            "0": "no diagnostic at or above the failure threshold",
            "1": "diagnostics at or above the failure threshold (or new findings for diff)",
            "2": "usage, project, baseline or I/O error",
            "3": "unexpected internal error (traceback on standard error)",
        },
        "error_codes": {
            "project-error": "project.godot could not be located or read",
            "inspect-error": "context, refs, graph, stats, discover or browse could not satisfy the request",
            "baseline-error": "the baseline is missing, not JSON, or not a supported report",
            "unknown-rule": "explain or a rule filter named an undocumented rule",
            "io-error": "output or source I/O failed",
            "cli-error": "other invalid command input",
        },
        "formats": {
            "text": "human-readable default",
            "json": "scan report v2; stable machine interface",
            "ndjson": "one meta line, one line per diagnostic, one summary line",
            "sarif": "SARIF 2.1.0 for code scanning",
            "github": "GitHub Actions workflow commands on standard output",
        },
        "commands": commands,
        "report_schema": REPORT_SCHEMA,
        "diff": {
            "semantics": (
                "fingerprints compared by count; new and resolved lists include only changes, "
                "persisting is a count. The same filters apply to the current scan and the "
                "baseline; hidden and baseline_hidden count removed rows. Exit 1 on new "
                "errors by default, or new warnings with --strict."
            ),
            "summary_keys": [
                "new",
                "resolved",
                "persisting",
                "hidden",
                "baseline_hidden",
            ],
        },
    }
    text = dumps(payload)
    _emit(text, args.output)
    return 0


HANDLERS = {
    "scan": _scan,
    "rules": _rules,
    "explain": _explain,
    "context": _context,
    "refs": _refs,
    "graph": _graph,
    "stats": _stats,
    "discover": _discover,
    "browse": _browse,
    "diff": _diff,
    "describe": _describe,
}


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "help":
        return _help(argv[1:])
    if argv and argv[0] in ("-h", "--help"):
        build_parser().print_help()
        return 0
    if argv == ["--version"]:
        print(f"godot-doctor {__version__}")
        return 0
    if not argv or argv[0] not in COMMANDS:
        argv = ["scan", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = HANDLERS.get(args.command)
    if handler is None:
        parser.error(f"unknown command {args.command!r}")
    try:
        return handler(args, parser)
    except CliError as exc:
        return _fail(args, exc.code, str(exc))
    except ProjectError as exc:
        return _fail(args, "project-error", str(exc))
    except InspectError as exc:
        return _fail(args, "inspect-error", str(exc))
    except BaselineError as exc:
        return _fail(args, "baseline-error", str(exc))
    except OSError as exc:
        return _fail(args, "io-error", str(exc))
    except Exception:
        traceback.print_exc()
        return 3


def gui_main() -> int:
    return main(["--gui", *sys.argv[1:]])
