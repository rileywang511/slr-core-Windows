from __future__ import annotations

from pathlib import Path

from slr.importers import OpenAlexCsvImporter, OpenAlexJsonImporter, detect_format


FIXTURES = Path(__file__).parent / "fixtures"


def test_openalex_csv_parses_all_rows():
    res = OpenAlexCsvImporter().parse(FIXTURES / "openalex.csv")
    assert res.n_records_parsed == 3
    first = res.records[0]
    assert first.source == "openalex"
    assert first.source_id == "W4012345678"           # extracted from URL form
    assert first.doi == "10.1038/nbt.5555"             # normalized (lowercase, no prefix)
    assert first.title == "CRISPR off-target effects in cell lines"
    assert first.year == 2024
    assert first.venue == "Nature Biotechnology"
    assert first.type == "article"
    assert first.url == "https://example.org/foo.pdf"
    # ' | '-separated authors with "Family Given" form
    assert {a.family for a in first.authors} == {"Smith", "Jones"}
    assert first.authors[0].given == "John"


def test_openalex_csv_handles_missing_doi():
    res = OpenAlexCsvImporter().parse(FIXTURES / "openalex.csv")
    third = res.records[2]
    assert third.doi is None
    # Still has the OpenAlex W-id
    assert third.source_id == "W4034567890"


def test_openalex_jsonl_reuses_normalize():
    res = OpenAlexJsonImporter().parse(FIXTURES / "openalex.jsonl")
    assert res.n_records_parsed == 3
    first = res.records[0]
    assert first.source == "openalex"
    assert first.source_id == "W4012345678"
    assert first.doi == "10.1038/nbt.5555"
    # Abstract reconstructed from inverted index by the normalize() reuse
    assert first.abstract == "This is an abstract"
    assert first.venue == "Nature Biotechnology"
    assert first.pmid == "38000001"
    assert {a.family for a in first.authors} == {"Smith", "Jones"}


def test_openalex_full_api_response():
    res = OpenAlexJsonImporter().parse(FIXTURES / "openalex_api_response.json")
    assert res.n_records_parsed == 2
    dois = {r.doi for r in res.records}
    assert "10.1093/nar/gkad999" in dois
    assert "10.1126/science.abc123" in dois


def test_detect_picks_openalex_csv():
    assert detect_format(FIXTURES / "openalex.csv") == "openalex"


def test_detect_picks_openalex_jsonl():
    assert detect_format(FIXTURES / "openalex.jsonl") == "openalex_json"


def test_detect_picks_openalex_json():
    assert detect_format(FIXTURES / "openalex_api_response.json") == "openalex_json"


def test_openalex_csv_not_confused_with_generic_csv(tmp_path: Path):
    """A generic CSV without OpenAlex signals shouldn't be claimed by the
    OpenAlex importer just because it has a title column."""
    p = tmp_path / "generic.csv"
    p.write_text("id,title,year\n1,Some paper,2024\n")
    detected = detect_format(p)
    assert detected != "openalex"


def test_openalex_jsonl_handles_single_object_file(tmp_path: Path):
    """A single-work file (JSON) should be parsed as one record."""
    p = tmp_path / "single.json"
    p.write_text(
        '{"id": "https://openalex.org/W1", "display_name": "Solo", '
        '"publication_year": 2024, "type": "article", "ids": {}, '
        '"authorships": []}'
    )
    res = OpenAlexJsonImporter().parse(p)
    assert res.n_records_parsed == 1
    assert res.records[0].source_id == "W1"
