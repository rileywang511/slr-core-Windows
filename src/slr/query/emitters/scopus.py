"""Scopus query emitter.

Scopus uses TITLE-ABS-KEY(), AUTHOR-NAME(), PUBYEAR predicates joined by AND/OR/NOT.
Phrases need braces or quotes: {exact phrase} or "exact phrase".
"""

from __future__ import annotations

from dataclasses import dataclass

from slr.query.ast import And, Expr, Near, Not, Or, Term, YearRange


_FIELD_MAP = {
    "TI": "TITLE",
    "AB": "ABS",
    "AU": "AUTHOR-NAME",
    "SO": "SRCTITLE",
    "DT": "DOCTYPE",
    "LA": "LANGUAGE",
    "TS": "TITLE-ABS-KEY",
}


@dataclass
class EmitResult:
    query_string: str
    warnings: list[str]


class ScopusEmitter:
    name = "scopus"

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def emit(self, expr: Expr, date_from: str | None = None, date_to: str | None = None) -> EmitResult:
        self.warnings = []
        rendered = self._render(expr)
        parts = [rendered] if rendered else []
        if date_from:
            year = int(date_from.split("-")[0])
            parts.append(f"PUBYEAR > {year - 1}")
        if date_to:
            year = int(date_to.split("-")[0])
            parts.append(f"PUBYEAR < {year + 1}")
        q = " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else (parts[0] if parts else "")
        return EmitResult(query_string=q, warnings=list(self.warnings))

    def _render(self, expr: Expr) -> str:
        if isinstance(expr, Term):
            value = f'"{expr.value}"' if expr.is_phrase else expr.value
            field = _FIELD_MAP.get(expr.field or "TS", "TITLE-ABS-KEY")
            return f"{field}({value})"
        if isinstance(expr, YearRange):
            return f"PUBYEAR > {expr.start - 1} AND PUBYEAR < {expr.end + 1}"
        if isinstance(expr, And):
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        if isinstance(expr, Or):
            return f"({self._render(expr.left)} OR {self._render(expr.right)})"
        if isinstance(expr, Not):
            return f"AND NOT {self._render(expr.operand)}"
        if isinstance(expr, Near):
            # Scopus supports W/n proximity
            return f"({self._render(expr.left)} W/{expr.distance} {self._render(expr.right)})"
        return ""
