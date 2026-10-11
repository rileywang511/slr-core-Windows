from __future__ import annotations

from slr.query import parse
from slr.query.emitters import (
    CrossrefEmitter,
    PubMedEmitter,
    ScopusEmitter,
    WosEmitter,
)


def test_pubmed_title_abstract_default():
    expr = parse("TS=(CRISPR)")
    result = PubMedEmitter().emit(expr)
    assert "CRISPR[Title/Abstract]" in result.query_string


def test_pubmed_author_mapping():
    expr = parse("AU=Smith")
    result = PubMedEmitter().emit(expr)
    assert "Smith[Author]" in result.query_string


def test_pubmed_year_range_pdat():
    expr = parse("PY=2015-2020")
    result = PubMedEmitter().emit(expr)
    assert '"2015"[PDAT]' in result.query_string
    assert '"2020"[PDAT]' in result.query_string


def test_crossref_lifts_year_filter():
    expr = parse("TS=(crispr) AND PY=2020-2024")
    result = CrossrefEmitter().emit(expr)
    assert result.filters["from-pub-date"] == "2020-01-01"
    assert result.filters["until-pub-date"] == "2024-12-31"
    assert "crispr" in result.query


def test_crossref_warns_on_or():
    expr = parse("TS=(a OR b)")
    result = CrossrefEmitter().emit(expr)
    assert any("OR semantics lost" in w for w in result.warnings)


def test_scopus_title_abs_key():
    expr = parse("TS=(crispr)")
    result = ScopusEmitter().emit(expr)
    assert "TITLE-ABS-KEY(crispr)" in result.query_string


def test_scopus_author_field():
    expr = parse("AU=Smith")
    result = ScopusEmitter().emit(expr)
    assert "AUTHOR-NAME(Smith)" in result.query_string


def test_scopus_proximity_native():
    expr = parse("TS=(foo NEAR/3 bar)")
    result = ScopusEmitter().emit(expr)
    assert "W/3" in result.query_string
    assert not result.warnings  # Scopus supports proximity natively


def test_wos_identity_roundtrip_basic():
    expr = parse('TS=("CRISPR" AND "off-target") AND PY=2023-2026')
    result = WosEmitter().emit(expr)
    assert "TS=" in result.query_string
    assert "PY=(2023-2026)" in result.query_string


def test_wos_proximity_preserved():
    expr = parse("TS=(foo NEAR/3 bar)")
    result = WosEmitter().emit(expr)
    assert "NEAR/3" in result.query_string
