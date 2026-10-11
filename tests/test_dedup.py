from __future__ import annotations

import json
from pathlib import Path

from slr.dedup import deduplicate_occurrences
from slr import frames
from slr.record import Author, Record


def _row(id, doi=None, title="Some title", year=2020, authors=None, abstract=None):
    return {
        "id": id,
        "doi": doi,
        "title": title,
        "year": year,
        "authors_json": json.dumps(authors or [{"family": "Smith", "given": "J"}]),
        "abstract": abstract,
    }


def test_doi_groups_merge():
    rows = [
        _row(1, doi="10.1/abc", title="Foo"),
        _row(2, doi="10.1/abc", title="Foo (different capitalization)", abstract="longer"),
        _row(3, doi="10.1/xyz", title="Bar"),
    ]
    plans = deduplicate_occurrences(rows)
    methods = sorted([p.method for p in plans])
    assert methods == ["doi", "singleton"]
    doi_group = next(p for p in plans if p.method == "doi")
    assert sorted(m["source_occurrence_id"] for m in doi_group.members) == [1, 2]
    # Canonical should be the one with the abstract (more complete)
    assert doi_group.canonical_occurrence_id == 2


def test_fuzzy_groups_no_doi():
    rows = [
        _row(1, doi=None, title="A study of metagenomic surveillance in wastewater"),
        _row(2, doi=None, title="A study of metagenomic surveillance in wastewater."),  # punct diff
        _row(3, doi=None, title="Unrelated paper about cancer therapy"),
    ]
    plans = deduplicate_occurrences(rows, fuzzy_threshold=90)
    assert len(plans) == 2
    fuzzy = next(p for p in plans if p.method == "fuzzy_title_author_year")
    assert sorted(m["source_occurrence_id"] for m in fuzzy.members) == [1, 2]


def test_fuzzy_blocks_on_year():
    rows = [
        _row(1, doi=None, title="Same title", year=2020),
        _row(2, doi=None, title="Same title", year=2021),  # different year -> different block
    ]
    plans = deduplicate_occurrences(rows)
    assert len(plans) == 2
    assert all(p.method == "singleton" for p in plans)


def test_fuzzy_merges_an_equivalent_no_doi_record_into_a_doi_group():
    rows = [
        _row(1, doi="10.1/abc", title="A study of metagenomic surveillance in wastewater"),
        _row(2, doi=None, title="A study of metagenomic surveillance in wastewater."),
    ]

    plans = deduplicate_occurrences(rows, fuzzy_threshold=90)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.method == "doi_fuzzy_title_author_year"
    assert plan.canonical_occurrence_id == 1  # DOI-bearing evidence stays canonical.
    evidence = {member["source_occurrence_id"]: member for member in plan.members}
    assert evidence[1]["match_method"] == "doi"
    assert evidence[2]["match_method"] == "fuzzy_title_author_year"
    assert evidence[2]["similarity"] == 1.0


def test_fuzzy_matching_never_merges_two_different_dois():
    rows = [
        _row(1, doi="10.1/abc", title="Identical title"),
        _row(2, doi="10.1/xyz", title="Identical title"),
    ]

    plans = deduplicate_occurrences(rows, fuzzy_threshold=90)

    assert len(plans) == 2
    assert all(plan.method == "singleton" for plan in plans)


def test_doi_fuzzy_bridge_is_persisted_as_reviewable_provenance(tmp_path: Path):
    frames.init(tmp_path)
    source_run_id = frames.append_source_run(
        tmp_path, "source", "query", "translated", [], "0.1.0", 2,
    )
    frames.append_provenance(tmp_path, source_run_id, [
        Record(
            source="source", source_id="with-doi", doi="10.1/abc",
            title="Wastewater metagenomic surveillance", year=2024,
            authors=[Author(family="Smith")],
        ),
        Record(
            source="source", source_id="without-doi", doi=None,
            title="Wastewater metagenomic surveillance.", year=2024,
            authors=[Author(family="Smith")],
        ),
    ])

    frames.write_deduplicated_records(
        tmp_path, [
            {
                "canonical_occurrence_id": plan.canonical_occurrence_id,
                "method": plan.method,
                "members": plan.members,
            }
            for plan in deduplicate_occurrences(frames.read_provenance(tmp_path))
        ],
    )

    assert len(frames.read_records(tmp_path)) == 1
    evidence = {row["source_id"]: row for row in frames.read_provenance(tmp_path)}
    assert evidence["with-doi"]["match_method"] == "doi"
    assert evidence["without-doi"]["match_method"] == "fuzzy_title_author_year"
    assert evidence["without-doi"]["similarity"] == 1.0


def test_doi_normalization_via_record_model():
    from slr.record import normalize_doi
    assert normalize_doi("https://doi.org/10.1/ABC") == "10.1/abc"
    assert normalize_doi("doi:10.1/ABC") == "10.1/abc"
    assert normalize_doi(None) is None
    assert normalize_doi("") is None
