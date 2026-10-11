from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

FieldTag = Literal["TS", "TI", "AB", "AU", "PY", "SO", "DT", "LA"]
ALL_FIELDS: tuple[FieldTag, ...] = ("TS", "TI", "AB", "AU", "PY", "SO", "DT", "LA")


@dataclass
class Term:
    value: str
    field: FieldTag | None = None
    is_phrase: bool = False

    @property
    def has_wildcard(self) -> bool:
        return "*" in self.value or "?" in self.value


@dataclass
class YearRange:
    start: int
    end: int
    field: FieldTag = "PY"


@dataclass
class And:
    left: "Expr"
    right: "Expr"


@dataclass
class Or:
    left: "Expr"
    right: "Expr"


@dataclass
class Not:
    operand: "Expr"


@dataclass
class Near:
    left: "Expr"
    right: "Expr"
    distance: int = 5


Expr = Union[Term, YearRange, And, Or, Not, Near]


def apply_field(expr: Expr, field_tag: FieldTag) -> Expr:
    """Walk expr; on any Term with no field set, assign field_tag. Returns expr (mutated)."""
    if isinstance(expr, Term):
        if expr.field is None:
            expr.field = field_tag
    elif isinstance(expr, (And, Or, Near)):
        apply_field(expr.left, field_tag)
        apply_field(expr.right, field_tag)
    elif isinstance(expr, Not):
        apply_field(expr.operand, field_tag)
    return expr


def Field(field_tag: FieldTag, inner: Expr) -> Expr:
    """Helper used by parser to assign a field tag to a sub-expression."""
    return apply_field(inner, field_tag)
