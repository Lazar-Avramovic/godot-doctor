from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: str
    category: str
    description: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


RULES: tuple[Rule, ...] = (
    Rule(
        id="broken-reference",
        title="Missing resource reference",
        severity="error",
        category="references",
        description=(
            "A scene, resource, script or project setting names a res:// path that does not "
            "exist in the project. Godot cannot load the resource."
        ),
        remediation=(
            "Correct the path, restore the missing file, or remove the reference. Check "
            "case and the res:// prefix; generated files under .godot are not project content."
        ),
    ),
    Rule(
        id="case-mismatch",
        title="Reference matches only on a case-insensitive filesystem",
        severity="warning",
        category="references",
        description=(
            "The exact path does not exist, but a file with the same name in a different case "
            "does. This works on default Windows and macOS volumes and breaks on Linux or any "
            "case-sensitive export target."
        ),
        remediation="Rename the reference or the file so the spelling and case match exactly.",
    ),
    Rule(
        id="invalid-resource-path",
        title="Resource path escapes the project root",
        severity="error",
        category="references",
        description=(
            "A res:// reference is malformed or normalises to a path outside the project root. "
            "Godot cannot resolve it."
        ),
        remediation="Use a path that stays inside res:// and contains no leading ../ segments.",
    ),
    Rule(
        id="resource-is-directory",
        title="Resource path names a directory",
        severity="error",
        category="references",
        description=(
            "A reference resolves to an existing directory or to the project root rather than "
            "to a resource file."
        ),
        remediation="Point the reference at a file, not a folder.",
    ),
    Rule(
        id="dependency-cycle",
        title="Eager dependency cycle",
        severity="warning",
        category="dependencies",
        description=(
            "Two or more scenes or resources eagerly depend on each other through ext_resource "
            "entries or preload(). Loading one may recurse indefinitely depending on when Godot "
            "evaluates the reference."
        ),
        remediation=(
            "Break the cycle by loading one side lazily with load(), moving shared content into "
            "a third resource, or removing the back-reference."
        ),
    ),
    Rule(
        id="duplicate-uid",
        title="Duplicate resource UID",
        severity="error",
        category="uids",
        description=(
            "Two files declare the same Godot 4 UID, from scene or resource headers, .uid "
            "sidecars (Godot 4.4+) or .import remap metadata. UID lookups become ambiguous and "
            "can resolve to the wrong resource."
        ),
        remediation=(
            "Keep one owner of the UID and let Godot regenerate the other (.uid sidecars and "
            ".import files are regenerated when removed); no other file is modified by this tool."
        ),
    ),
    Rule(
        id="parse-error",
        title="File could not be parsed",
        severity="error",
        category="parsing",
        description=(
            "A scene, resource, script or metadata file is unreadable or contains structure the "
            "parser rejects, so its references could not be analysed. The message names the "
            "specific defect, such as an ext_resource without an id or path. Some structural "
            "defects, such as an unrecognised instance reference, are reported as warnings."
        ),
        remediation="Fix the reported line or encoding and run the scan again.",
    ),
    Rule(
        id="duplicate-ext-resource",
        title="Duplicate ext_resource id",
        severity="error",
        category="parsing",
        description=(
            "One scene declares the same ext_resource id more than once, so ExtResource() "
            "lookups are ambiguous."
        ),
        remediation="Give each ext_resource a unique id and update its references.",
    ),
    Rule(
        id="invalid-ext-resource",
        title="ext_resource path is not res://",
        severity="warning",
        category="parsing",
        description=(
            "An ext_resource entry does not use a res:// path, which this analyser cannot resolve "
            "or verify."
        ),
        remediation="Use a res:// path for project resources and keep the uid attribute in sync.",
    ),
    Rule(
        id="undefined-ext-resource",
        title="ExtResource id is not declared",
        severity="error",
        category="parsing",
        description=(
            "A node or property uses ExtResource(\"id\") but the scene declares no ext_resource "
            "with that id. The scene cannot load that dependency."
        ),
        remediation="Declare the missing ext_resource or correct the id.",
    ),
    Rule(
        id="undefined-sub-resource",
        title="SubResource id is not declared",
        severity="error",
        category="parsing",
        description=(
            "A node or property uses SubResource(\"id\") but the scene declares no sub_resource "
            "with that id."
        ),
        remediation="Declare the missing sub_resource or correct the id.",
    ),
    Rule(
        id="config-hash-comment",
        title="'#' line in project.godot is not a comment",
        severity="error",
        category="project",
        description=(
            "A '#' line is not a comment in Godot's ConfigFile format: it is parsed as key "
            "text and folded into the next key, so the setting that follows can vanish while "
            "ProjectSettings.has_setting() still returns true. This has silently emptied a "
            "project setting before."
        ),
        remediation=(
            "Use ';' for comments, or run 'godot-doctor fix --apply' to repair leading '#' "
            "lines automatically. Re-scan after the fix."
        ),
    ),
    Rule(
        id="config-nonkey-line",
        title="project.godot line is not key=value",
        severity="info",
        category="project",
        description=(
            "A non-empty line outside a section header has no '='. Godot ignores the line, so "
            "it is informational; it usually means a malformed or hand-edited config. "
            "Multi-line values using { } or [ ] are parsed and are not flagged."
        ),
        remediation=(
            "Remove the line, complete it as key=value, or use ';' for a comment. Godot "
            "ignores this line, so it does not fail a scan."
        ),
    ),
    Rule(
        id="config-unclosed-value",
        title="project.godot value is never closed",
        severity="error",
        category="project",
        description=(
            "A value opened with '{' or '[' never balances before the file ends. Godot "
            "cannot parse the rest of the file, so every later setting is lost. This is "
            "typical of a hand-edited or truncated project.godot."
        ),
        remediation=(
            "Close the brace or bracket, or remove the malformed value, then scan again."
        ),
    ),
    Rule(
        id="unreadable-directory",
        title="Directory cannot be read",
        severity="warning",
        category="project",
        description=(
            "The filesystem refused to open a directory during the scan, so resources inside it "
            "were not indexed and references to them may be reported as missing."
        ),
        remediation=(
            "Fix the directory permissions or remove it from the project tree, then scan again."
        ),
    ),
)

RULEBOOK: dict[str, Rule] = {rule.id: rule for rule in RULES}


def get_rule(rule_id: str) -> Rule | None:
    return RULEBOOK.get(rule_id)


def list_rules() -> list[Rule]:
    return list(RULES)
