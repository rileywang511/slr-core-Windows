from __future__ import annotations

from slr.query import parse
from slr.query.emitters import EuropePMCEmitter, OpenAlexEmitter


def test_openalex_basic_and():
    expr = parse("TS=(metagenomics AND wastewater)")
    result = OpenAlexEmitter().emit(expr)
    assert "default.search:(metagenomics AND wastewater)" in result.filter_string


def test_openalex_phrase_quoted():
    expr = parse('TS=("shotgun sequencing" OR foo)')
    result = OpenAlexEmitter().emit(expr)
    assert '"shotgun sequencing"' in result.filter_string


def test_openalex_year_range_lifted():
    expr = parse('TS=(foo) AND PY=2015-2026')
    result = OpenAlexEmitter().emit(expr)
    assert "from_publication_date:2015-01-01" in result.filter_string
    assert "to_publication_date:2026-12-31" in result.filter_string
    assert "default.search:foo" in result.filter_string


def test_openalex_review_type_filter():
    expr = parse('TS=(foo) AND DT=Review')
    result = OpenAlexEmitter().emit(expr)
    assert "type:review" in result.filter_string


def test_openalex_near_warns_and_degrades():
    expr = parse("TS=(foo NEAR/3 bar)")
    result = OpenAlexEmitter().emit(expr)
    assert any("NEAR" in w for w in result.warnings)
    assert "foo AND bar" in result.filter_string


def test_europepmc_basic_and():
    expr = parse("TS=(metagenomics AND wastewater)")
    result = EuropePMCEmitter().emit(expr)
    assert "metagenomics" in result.query_string
    assert "wastewater" in result.query_string
    assert "AND" in result.query_string


def test_europepmc_title_scope_mapped():
    expr = parse('TI=(cancer)')
    result = EuropePMCEmitter().emit(expr)
    assert "TITLE:cancer" in result.query_string


def test_europepmc_phrase_quoted():
    expr = parse('TI=("CRISPR off-target")')
    result = EuropePMCEmitter().emit(expr)
    assert 'TITLE:"CRISPR off-target"' in result.query_string


def test_europepmc_year_range():
    expr = parse('TS=(foo) AND PY=2015-2026')
    result = EuropePMCEmitter().emit(expr)
    assert "PUB_YEAR:[2015 TO 2026]" in result.query_string


def test_europepmc_near_warns():
    expr = parse("TS=(foo NEAR/3 bar)")
    result = EuropePMCEmitter().emit(expr)
    assert any("NEAR" in w for w in result.warnings)


def test_europepmc_pub_type_mapped():
    expr = parse('TS=(foo) NOT DT=Review')
    result = EuropePMCEmitter().emit(expr)
    assert "PUB_TYPE:Review" in result.query_string
    assert "NOT" in result.query_string
