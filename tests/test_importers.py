from __future__ import annotations

import json
from pathlib import Path

import pytest

from slr.importers import (
    EuropePMCCsvImporter,
    GenericCsvImporter,
    PubMedCsvImporter,
    RisImporter,
    ScopusCsvImporter,
    WosCsvImporter,
    detect_format,
)
from slr.importers.base import ImportWarning
from slr import frames

FIXTURES = Path(__file__).parent / "fixtures"


def test_scopus_importer_parses_all_rows():
    res = ScopusCsvImporter().parse(FIXTURES / "scopus.csv")
    assert res.n_rows_read == 3
    assert res.n_records_parsed == 3
    titles = [r.title for r in res.records]
    assert "Off-target effects in CRISPR-Cas9" in titles
    smith = res.records[0]
    assert smith.source == "scopus"
    assert smith.doi == "10.1038/nbt.1234"
    assert smith.year == 2024
    assert smith.venue == "Nature Biotechnology"
    assert smith.type == "Article"
    assert smith.source_id == "2-s2.0-12345"
    assert smith.authors[0].family == "Smith"
    # Scopus exports "J." but our normaliser strips trailing dots
    assert smith.authors[0].given in ("J.", "J")
    assert smith.authors[1].family == "Jones"


def test_scopus_synthesizes_id_when_eid_missing():
    """The third row in scopus.csv has empty EID — should get a synthesized id."""
    res = ScopusCsvImporter().parse(FIXTURES / "scopus.csv")
    third = res.records[2]
    assert "scopus.csv#" in third.source_id  # synthesized form


def test_wos_importer_parses_full_names():
    res = WosCsvImporter().parse(FIXTURES / "wos.csv")
    assert res.n_records_parsed == 3
    smith = res.records[0]
    assert smith.source == "wos"
    assert smith.doi == "10.1038/nbt.5555"
    assert smith.pmid == "38000001"
    assert smith.source_id == "WOS:000111111111"
    # AF was used — should have full first name "John"
    assert smith.authors[0].family == "Smith"
    assert smith.authors[0].given == "John"


def test_pubmed_importer():
    res = PubMedCsvImporter().parse(FIXTURES / "pubmed.csv")
    assert res.n_records_parsed == 3
    first = res.records[0]
    assert first.source == "pubmed"
    assert first.pmid == "38000001"
    assert first.pmcid == "PMC11111111"
    assert first.doi == "10.1038/nbt.5555"
    assert first.source_id == "38000001"
    assert first.url == "https://pubmed.ncbi.nlm.nih.gov/38000001/"
    # PubMed authors are comma-separated: "Smith J, Jones K"
    assert {a.family for a in first.authors} == {"Smith", "Jones"}


def test_europepmc_importer():
    res = EuropePMCCsvImporter().parse(FIXTURES / "europepmc.csv")
    assert res.n_records_parsed == 3
    first = res.records[0]
    assert first.source == "europepmc"
    assert first.doi == "10.1038/nbt.5555"
    assert first.pmid == "38000001"
    assert first.pmcid == "PMC11111111"
    assert first.url == "https://europepmc.org/article/PMC/PMC11111111"
    # authorString = "Smith J, Jones K." — trailing dot stripped, comma-split
    assert {a.family for a in first.authors} == {"Smith", "Jones"}


def test_generic_csv_with_semicolon_authors():
    res = GenericCsvImporter().parse(FIXTURES / "generic.csv")
    assert res.n_records_parsed == 3
    first = res.records[0]
    assert first.title == "CRISPR off-target effects in cell lines"
    assert first.doi == "10.1038/nbt.5555"
    assert first.year == 2024
    assert first.source == "csv"
    assert {a.family for a in first.authors} == {"Smith", "Jones"}


def test_ris_importer():
    res = RisImporter().parse(FIXTURES / "sample.ris")
    assert res.n_records_parsed == 3
    first = res.records[0]
    assert first.title == "CRISPR off-target effects in cell lines"
    assert first.doi == "10.1038/nbt.5555"
    assert first.year == 2024
    assert first.source == "ris"
    assert first.venue == "Nature Biotechnology"
    assert {a.family for a in first.authors} == {"Smith", "Jones"}


def test_doi_normalized_to_lowercase():
    """The Record model lowercases DOIs — important for cross-source dedup."""
    res = WosCsvImporter().parse(FIXTURES / "wos.csv")
    pubmed_res = PubMedCsvImporter().parse(FIXTURES / "pubmed.csv")
    wos_dois = {r.doi for r in res.records if r.doi}
    pm_dois = {r.doi for r in pubmed_res.records if r.doi}
    # The same paper appears in both with the same DOI — dedup should merge them
    assert "10.1038/nbt.5555" in wos_dois
    assert "10.1038/nbt.5555" in pm_dois


# ---- auto-detection ----

@pytest.mark.parametrize("filename,expected", [
    ("scopus.csv", "scopus"),
    ("wos.csv", "wos"),
    ("pubmed.csv", "pubmed"),
    ("europepmc.csv", "europepmc"),
    ("generic.csv", "csv"),
    ("sample.ris", "ris"),
])
def test_detect_format(filename: str, expected: str):
    assert detect_format(FIXTURES / filename) == expected


def test_detect_format_unknown_columns(tmp_path: Path):
    p = tmp_path / "weird.csv"
    p.write_text("foo,bar,baz\n1,2,3\n")
    with pytest.raises(RuntimeError):
        detect_format(p)


# ---- re-import idempotency ----

def test_reimport_is_idempotent(tmp_path: Path):
    """Same file imported twice should not duplicate source occurrences."""
    from tests import ReviewFiles
    store = ReviewFiles(tmp_path)
    store.init()
    run_id = store.insert_source_run("scopus", "q", "tq", [], "0.0", 3)
    res = ScopusCsvImporter().parse(FIXTURES / "scopus.csv")
    n1 = store.insert_records(run_id, res.records)
    n2 = store.insert_records(run_id, res.records)
    assert n1 == 3
    assert n2 == 0  # all rows are duplicates of the first import


def test_import_warning_retains_the_original_failed_row(tmp_path: Path):
    frames.init(tmp_path)
    failed_row = {"Title": "Unparseable export row", "DOI": "bad"}
    frames.append_source_run(
        tmp_path, "csv", "<IMPORT FROM export.csv>", "importer=csv",
        [ImportWarning(7, "invalid field", failed_row)], "0.1", 1, run_kind="import",
    )

    events = [json.loads(line) for line in (tmp_path / frames.EVIDENCE).read_text().splitlines()]
    warning = next(event for event in events if event["type"] == "warning")
    assert warning["row_number"] == 7
    assert warning["raw"] == failed_row


def test_import_warning_with_unnamed_csv_columns_is_recorded(tmp_path: Path):
    """Surplus CSV values used to make sorted JSON serialization crash."""
    frames.init(tmp_path)
    failed_row = {"Title": "Unparseable export row", None: ["surplus value"]}

    frames.append_source_run(
        tmp_path, "csv", "<IMPORT FROM export.csv>", "importer=csv",
        [ImportWarning(7, "invalid field", failed_row)], "0.1", 1, run_kind="import",
    )

    events = [json.loads(line) for line in (tmp_path / frames.EVIDENCE).read_text().splitlines()]
    warning = next(event for event in events if event["type"] == "warning")
    assert warning["raw"]["<unnamed column>"] == ["surplus value"]
