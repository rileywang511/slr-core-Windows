"""Web of Science query emitter.

WoS Advanced Search syntax is essentially what our parser already accepts; this
emitter is mostly the identity function on the AST -> canonical WoS syntax,
re-rendering for transmission via the WoS Lite/Expanded API.
"""

from __future__ import annotations

from dataclasses import dataclass

from slr.query.ast import And, Expr, Near, Not, Or, Term, YearRange


@dataclass
class EmitResult:
    query_string: str
    warnings: list[str]


class WosEmitter:
    name = "wos"

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def emit(self, expr: Expr, date_from: str | None = None, date_to: str | None = None) -> EmitResult:
        self.warnings = []
        rendered = self._render(expr)
        parts = [rendered] if rendered else []
        if date_from or date_to:
            df = date_from or "1900-01-01"
            dt = date_to or "3000-12-31"
            parts.append(f"DOP=({df}/{dt})")
        q = " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else (parts[0] if parts else "")
        return EmitResult(query_string=q, warnings=list(self.warnings))

    def _render(self, expr: Expr) -> str:
        if isinstance(expr, Term):
            value = f'"{expr.value}"' if expr.is_phrase else expr.value
            if expr.field:
                return f"{expr.field}=({value})"
            return value
        if isinstance(expr, YearRange):
            return f"PY=({expr.start}-{expr.end})"
        if isinstance(expr, And):
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        if isinstance(expr, Or):
            return f"({self._render(expr.left)} OR {self._render(expr.right)})"
        if isinstance(expr, Not):
            return f"NOT ({self._render(expr.operand)})"
        if isinstance(expr, Near):
            return f"({self._render(expr.left)} NEAR/{expr.distance} {self._render(expr.right)})"
        return ""
