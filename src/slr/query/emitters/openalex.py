"""OpenAlex query emitter.

OpenAlex API uses two relevant mechanisms:
  - `filter=` clauses joined by commas (AND), supporting field-specific predicates
  - `*.search:...` filters whose value accepts AND / OR / NOT / "phrase" / (groups)

Strategy: walk the AST; lift PY / DT / LA into separate filter clauses; render
the remaining boolean expression into `default.search:...`. Field tags TI/AB/AU
are best-effort folded into the search string (precision-loss warning emitted).
NEAR/n is unsupported and degrades to AND with a warning.
"""

from __future__ import annotations

from dataclasses import dataclass

from slr.query.ast import And, Expr, Near, Not, Or, Term, YearRange


_TYPE_MAP_LOWER = {
    "review": "review",
    "article": "article",
    "preprint": "preprint",
    "book": "book",
    "letter": "letter",
}


@dataclass
class EmitResult:
    filter_string: str         # value to pass as ?filter=...
    warnings: list[str]


class OpenAlexEmitter:
    name = "openalex"

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def emit(self, expr: Expr, date_from: str | None = None, date_to: str | None = None) -> EmitResult:
        self.warnings = []
        filters: list[str] = []
        # First-pass: lift year/type/language top-level conjunctions
        remaining = self._lift_filters(expr, filters)
        if remaining is not None:
            search_str = self._render(remaining)
            if search_str:
                filters.append(f"default.search:{search_str}")

        if date_from:
            filters.append(f"from_publication_date:{date_from}")
        if date_to:
            filters.append(f"to_publication_date:{date_to}")

        return EmitResult(filter_string=",".join(filters), warnings=list(self.warnings))

    # ---- filter lifting ----

    def _lift_filters(self, expr: Expr, out_filters: list[str]) -> Expr | None:
        """Recursively pull top-level AND-conjunctions of YearRange/DT/LA terms
        into out_filters, returning the rest of the expression (or None)."""
        if isinstance(expr, And):
            left = self._lift_filters(expr.left, out_filters)
            right = self._lift_filters(expr.right, out_filters)
            if left is None:
                return right
            if right is None:
                return left
            return And(left, right)
        if isinstance(expr, YearRange):
            out_filters.append(f"from_publication_date:{expr.start}-01-01")
            out_filters.append(f"to_publication_date:{expr.end}-12-31")
            return None
        if isinstance(expr, Term) and expr.field == "DT":
            t = _TYPE_MAP_LOWER.get(expr.value.lower())
            if t:
                out_filters.append(f"type:{t}")
            else:
                self.warnings.append(f"unmapped DT value: {expr.value!r}; emitted as search term")
                return expr
            return None
        if isinstance(expr, Term) and expr.field == "LA":
            out_filters.append(f"language:{expr.value.lower()}")
            return None
        return expr

    # ---- search-string rendering ----

    def _render(self, expr: Expr) -> str:
        if isinstance(expr, Term):
            if expr.field == "TI":
                self.warnings.append("TI scope folded into default.search; OpenAlex lacks AND-combinable per-field search")
            elif expr.field == "AB":
                self.warnings.append("AB scope folded into default.search; OpenAlex lacks AND-combinable per-field search")
            elif expr.field == "AU":
                self.warnings.append("AU scope folded into default.search; precision reduced")
            elif expr.field == "SO":
                self.warnings.append("SO (source) scope folded into default.search")
            return self._render_term(expr)
        if isinstance(expr, YearRange):
            self.warnings.append("nested YearRange found mid-expression; OpenAlex requires top-level date filters")
            return ""
        if isinstance(expr, And):
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        if isinstance(expr, Or):
            return f"({self._render(expr.left)} OR {self._render(expr.right)})"
        if isinstance(expr, Not):
            return f"NOT {self._render(expr.operand)}"
        if isinstance(expr, Near):
            self.warnings.append(f"NEAR/{expr.distance} degraded to AND; OpenAlex has no proximity operator")
            return f"({self._render(expr.left)} AND {self._render(expr.right)})"
        return ""

    def _render_term(self, t: Term) -> str:
        if t.is_phrase:
            return f"\"{t.value}\""
        return t.value
