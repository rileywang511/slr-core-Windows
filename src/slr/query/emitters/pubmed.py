"""PubMed E-utilities query emitter.

PubMed uses bracket-suffix field tags: cancer[Title], smith[Author], 2020[PDAT].
Boolean operators AND / OR / NOT (capitalised), grouping with parens.
"""

from __future__ import annotations

from dataclasses import dataclass

from slr.query.ast import And, Expr, Near, Not, Or, Term, YearRange


_FIELD_MAP = {
    "TI": "Title",
    "AB": "Title/Abstract",
    "AU": "Author",
    "SO": "Journal",
    "DT": "Publication Type",
    "LA": "Language",
    "TS": "Title/Abstract",
    "PY": "PDAT",
}


@dataclass
class EmitResult:
    query_string: str
    warnings: list[str]


class PubMedEmitter:
    name = "pubmed"

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def emit(self, expr: Expr, date_from: str | None = None, date_to: str | None = None) -> EmitResult:
        self.warnings = []
        parts = [self._render(expr)] if expr else []
        if date_from or date_to:
            df = (date_from or "1900/01/01").replace("-", "/")
            dt = (date_to or "3000/12/31").replace("-", "/")
            parts.append(f"({df}[PDAT] : {dt}[PDAT])")
        q = " AND ".join(f"({p})" for p in parts if p) if len(parts) > 1 else (parts[0] if parts else "")
        return EmitResult(query_string=q, warnings=list(self.warnings))

    def _render(self, expr: Expr) -> str:
        if isinstance(expr, Term):
            value = f'"{expr.value}"' if expr.is_phrase else expr.value
            if expr.field is None or expr.field == "TS":
                return f"{value}[Title/Abstract]"
            field = _FIELD_MAP.get(expr.field)
            if field is None:
                self.warnings.append(f"unknown field {expr.field}; emitted unscoped")
                return value
            return f"{value}[{field}]"
        if isinstance(expr, YearRange):
            return f'("{expr.start}"[PDAT] : "{expr.end}"[PDAT])'
        if isinstance(expr, And):
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        if isinstance(expr, Or):
            return f"({self._render(expr.left)} OR {self._render(expr.right)})"
        if isinstance(expr, Not):
            return f"NOT {self._render(expr.operand)}"
        if isinstance(expr, Near):
            self.warnings.append(f"NEAR/{expr.distance} degraded to AND for PubMed")
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        return ""
