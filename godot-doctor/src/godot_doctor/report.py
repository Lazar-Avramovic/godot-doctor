from __future__ import annotations

from .formats import format_json_report, format_text_report
from .rules import AnalysisResult


def format_text(result: AnalysisResult) -> str:
    return format_text_report(result)


def format_json(result: AnalysisResult) -> str:
    return format_json_report(result)
