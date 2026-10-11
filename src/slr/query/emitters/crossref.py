"""Crossref REST API query emitter.

Crossref has limited Boolean support; we send the bag-of-terms via `query=...`
and lift dates / types via filter params. Most precision is lost compared to
WoS-style queries — emit warnings accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from slr.query.ast import And, Expr, Near, Not, Or, Term, YearRange


@dataclass
class EmitResult:
    query: str
    filters: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class CrossrefEmitter:
    name = "crossref"

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def emit(self, expr: Expr, date_from: str | None = None, date_to: str | None = None) -> EmitResult:
        self.warnings = []
        filters: dict[str, str] = {}
        rest = self._lift_filters(expr, filters)
        words: list[str] = []
        self._collect_terms(rest, words)
        if date_from:
            filters["from-pub-date"] = date_from
        if date_to:
            filters["until-pub-date"] = date_to
        return EmitResult(query=" ".join(words), filters=filters, warnings=list(self.warnings))

    def _lift_filters(self, expr: Expr, out: dict[str, str]) -> Expr | None:
        if isinstance(expr, And):
            left = self._lift_filters(expr.left, out)
            right = self._lift_filters(expr.right, out)
            if left is None:
                return right
            if right is None:
                return left
            return And(left, right)
        if isinstance(expr, YearRange):
            out["from-pub-date"] = f"{expr.start}-01-01"
            out["until-pub-date"] = f"{expr.end}-12-31"
            return None
        if isinstance(expr, Term) and expr.field == "DT":
            t = expr.value.lower()
            mapping = {"review": "journal-article", "article": "journal-article", "book": "book"}
            out["type"] = mapping.get(t, t)
            return None
        return expr

    def _collect_terms(self, expr: Expr | None, out: list[str]) -> None:
        if expr is None:
            return
        if isinstance(expr, Term):
            if expr.is_phrase:
                out.append(f'"{expr.value}"')
            else:
                out.append(expr.value.rstrip("*?"))
            return
        if isinstance(expr, (And, Or)):
            self._collect_terms(expr.left, out)
            self._collect_terms(expr.right, out)
            if isinstance(expr, Or):
                self.warnings.append("Crossref query is a bag-of-words; OR semantics lost")
            return
        if isinstance(expr, Not):
            self.warnings.append("Crossref query is a bag-of-words; NOT semantics lost")
            return
        if isinstance(expr, Near):
            self.warnings.append("Crossref query is a bag-of-words; NEAR semantics lost")
            self._collect_terms(expr.left, out)
            self._collect_terms(expr.right, out)
            return
        if isinstance(expr, YearRange):
            return
