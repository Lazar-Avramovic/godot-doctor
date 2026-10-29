from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}[self]


@dataclass(frozen=True)
class Reference:
    raw: str
    file: str
    line: int
    column: int
    kind: str
    eager: bool = False


@dataclass(frozen=True)
class Diagnostic:
    rule: str
    severity: Severity
    message: str
    file: str
    line: int | None = None
    column: int | None = None
    related: tuple[str, ...] = ()
    # Stable subject (not prose) used for the fingerprint; rewordings keep it.
    anchor: str = ""

    def sort_key(self) -> tuple[object, ...]:
        return (
            self.file,
            self.line if self.line is not None else 0,
            self.column if self.column is not None else 0,
            self.rule,
            self.message,
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "file": self.file,
        }
        if self.line is not None:
            payload["line"] = self.line
        if self.column is not None:
            payload["column"] = self.column
        if self.related:
            payload["related"] = list(self.related)
        if self.anchor:
            payload["anchor"] = self.anchor
        return payload
