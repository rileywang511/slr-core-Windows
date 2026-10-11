"""Source.normalize() tests — no network."""
from __future__ import annotations

from slr.sources.openalex import OpenAlexSource
from slr.sources.europepmc import EuropePMCSource


def test_openalex_normalize_basic():
    raw = {
        "id": "https://openalex.org/W123",
        "doi": "https://doi.org/10.1/ABC",
        "display_name": "A study of X",
        "publication_year": 2024,
        "abstract_inverted_index": {"This": [0], "is": [1], "an": [2], "abstract": [3]},
        "authorships": [
            {
                "author": {"display_name": "Jane Q Smith", "orcid": "0000-0002-1825-0097"},
                "institutions": [{"display_name": "Cranfield Univ"}],
            },
        ],
        "primary_location": {"source": {"display_name": "Nature"}},
        "type": "article",
        "open_access": {"oa_url": "https://example.org/foo.pdf"},
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345", "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/PMC678"},
    }
    rec = OpenAlexSource().normalize(raw)
    assert rec.source == "openalex"
    assert rec.source_id == "W123"
    assert rec.doi == "10.1/abc"
    assert rec.title == "A study of X"
    assert rec.abstract == "This is an abstract"
    assert rec.year == 2024
    assert rec.venue == "Nature"
    assert rec.pmid == "12345"
    assert rec.pmcid == "PMC678"
    assert len(rec.authors) == 1
    assert rec.authors[0].family == "Smith"
    assert rec.authors[0].given == "Jane Q"
    assert rec.authors[0].affiliation == "Cranfield Univ"


def test_europepmc_normalize_basic():
    raw = {
        "id": "12345",
        "pmid": "12345",
        "pmcid": "PMC678",
        "doi": "10.1/xyz",
        "title": "A study of Y",
        "abstractText": "Some abstract.",
        "pubYear": "2023",
        "journalTitle": "PLoS One",
        "pubType": "Journal Article",
        "authorList": {
            "author": [
                {"lastName": "Smith", "firstName": "Jane", "affiliation": "Cranfield"},
                {"lastName": "Jones", "initials": "K"},
            ]
        },
    }
    rec = EuropePMCSource().normalize(raw)
    assert rec.source == "europepmc"
    assert rec.doi == "10.1/xyz"
    assert rec.title == "A study of Y"
    assert rec.year == 2023
    assert rec.venue == "PLoS One"
    assert rec.pmid == "12345"
    assert rec.pmcid == "PMC678"
    assert rec.url == "https://europepmc.org/article/PMC/PMC678"
    assert len(rec.authors) == 2
    assert rec.authors[0].family == "Smith"
    assert rec.authors[1].family == "Jones"
