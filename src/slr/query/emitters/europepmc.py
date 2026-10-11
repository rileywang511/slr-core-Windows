"""Europe PMC query emitter.

Europe PMC uses a Lucene-ish query DSL similar to PubMed. Field map:
  TI -> TITLE   AB -> ABSTRACT   AU -> AUTH    PY -> PUB_YEAR
  SO -> JOURNAL DT -> PUB_TYPE   LA -> LANG    TS -> (no field; default cross-field search)

Supports AND / OR / NOT, parentheses, "phrases", * wildcard.
NEAR/n is not supported -> degrade to AND with warning.
"""

from __future__ import annotations

from dataclasses import dataclass

from slr.query.ast import And, Expr, Near, Not, Or, Term, YearRange


_FIELD_MAP = {
    "TI": "TITLE",
    "AB": "ABSTRACT",
    "AU": "AUTH",
    "SO": "JOURNAL",
    "DT": "PUB_TYPE",
    "LA": "LANG",
    "PY": "PUB_YEAR",
    # TS: cross-field, default search — emitted unscoped
}


@dataclass
class EmitResult:
    query_string: str
    warnings: list[str]


class EuropePMCEmitter:
    name = "europepmc"

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def emit(self, expr: Expr, date_from: str | None = None, date_to: str | None = None) -> EmitResult:
        self.warnings = []
        rendered = self._render(expr)
        parts = [rendered] if rendered else []
        if date_from:
            parts.append(f"FIRST_PDATE:[{date_from} TO {date_to or '*'}]")
        elif date_to:
            parts.append(f"FIRST_PDATE:[* TO {date_to}]")
        q = " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else (parts[0] if parts else "")
        return EmitResult(query_string=q, warnings=list(self.warnings))

    def _render(self, expr: Expr) -> str:
        if isinstance(expr, Term):
            return self._render_term(expr)
        if isinstance(expr, YearRange):
            if expr.start == expr.end:
                return f"PUB_YEAR:{expr.start}"
            return f"PUB_YEAR:[{expr.start} TO {expr.end}]"
        if isinstance(expr, And):
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        if isinstance(expr, Or):
            return f"({self._render(expr.left)} OR {self._render(expr.right)})"
        if isinstance(expr, Not):
            return f"NOT {self._render(expr.operand)}"
        if isinstance(expr, Near):
            self.warnings.append(f"NEAR/{expr.distance} degraded to AND; Europe PMC has no proximity operator")
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        return ""

    def _render_term(self, t: Term) -> str:
        value = f'"{t.value}"' if t.is_phrase else t.value
        if t.field is None or t.field == "TS":
            return value
        field = _FIELD_MAP.get(t.field)
        if field is None:
            self.warnings.append(f"unknown field tag {t.field}; emitted unscoped")
            return value
        return f'{field}:{value}'
