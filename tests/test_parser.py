from __future__ import annotations

import pytest

from slr.query import parse
from slr.query.ast import And, Near, Not, Or, Term, YearRange


def test_simple_topic_and():
    expr = parse("TS=(metagenomics AND wastewater)")
    assert isinstance(expr, And)
    assert isinstance(expr.left, Term) and expr.left.field == "TS" and expr.left.value == "metagenomics"
    assert isinstance(expr.right, Term) and expr.right.field == "TS" and expr.right.value == "wastewater"


def test_phrase_and_wildcard():
    expr = parse('TS=("shotgun sequencing" OR metagenom*)')
    assert isinstance(expr, Or)
    assert isinstance(expr.left, Term) and expr.left.is_phrase and expr.left.value == "shotgun sequencing"
    assert isinstance(expr.right, Term) and expr.right.has_wildcard


def test_or_under_and_precedence():
    expr = parse("TS=(a AND b OR c)")
    # AND binds tighter than OR: (a AND b) OR c
    assert isinstance(expr, Or)
    assert isinstance(expr.left, And)


def test_year_range():
    expr = parse("PY=2015-2026")
    assert isinstance(expr, YearRange)
    assert (expr.start, expr.end) == (2015, 2026)


def test_year_single():
    expr = parse("PY=2020")
    assert isinstance(expr, YearRange) and expr.start == 2020 and expr.end == 2020


def test_compound_with_year_and_not():
    expr = parse('TS=(metagenom* AND wastewater) AND PY=2015-2026 NOT DT=Review')
    # Outermost should be `... AND (NOT DT=Review)`
    assert isinstance(expr, And)
    # Right operand is the NOT of DT=Review
    assert isinstance(expr.right, Not)
    inner = expr.right.operand
    assert isinstance(inner, Term) and inner.field == "DT"


def test_near_operator():
    expr = parse("TS=(foo NEAR/3 bar)")
    assert isinstance(expr, Near)
    assert expr.distance == 3


def test_near_default_distance():
    expr = parse("TS=(foo NEAR bar)")
    assert isinstance(expr, Near)
    assert expr.distance == 5


def test_unterminated_phrase_raises():
    from slr.query.parser import ParseError
    with pytest.raises(ParseError):
        parse('TS=("oops)')


def test_field_propagates_into_group():
    expr = parse("TI=(cancer OR tumor)")
    assert isinstance(expr, Or)
    assert isinstance(expr.left, Term) and expr.left.field == "TI"
    assert isinstance(expr.right, Term) and expr.right.field == "TI"


def test_author_field():
    expr = parse("AU=Smith AND PY=2020")
    assert isinstance(expr, And)
    assert isinstance(expr.left, Term) and expr.left.field == "AU"
    assert isinstance(expr.right, YearRange) and expr.right.start == 2020
