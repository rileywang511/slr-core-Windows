"""Deduplication.

Algorithm:
 1) DOI block — exact normalized DOI match -> deduplication group (method='doi', similarity=1.0)
 2) DOI bridge — a no-DOI occurrence can join a DOI group when title, first author,
    and year match fuzzily. Records with two different DOIs are never merged here.
 3) Fuzzy block — remaining no-DOI records are grouped by title/author/year.
 4) Canonical pick — within each group, prefer the occurrence with the most complete metadata.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from slr.normalize import normalize_lastname, normalize_title


@dataclass
class DedupPlan:
    canonical_occurrence_id: int
    method: str
    members: list[dict[str, Any]]  # {source_occurrence_id, similarity}


def _author_lastname(authors_json: str | None) -> str:
    if not authors_json:
        return ""
    try:
        authors = json.loads(authors_json)
    except json.JSONDecodeError:
        return ""
    for a in authors:
        if a.get("family"):
            return normalize_lastname(a["family"])
    return ""


def _completeness_score(row: dict[str, Any]) -> tuple[int, int, int]:
    """Higher is better. (has_doi, abstract_len, has_authors)."""
    return (
        1 if row.get("doi") else 0,
        len(row.get("abstract") or ""),
        1 if row.get("authors_json") and row["authors_json"] != "[]" else 0,
    )


def deduplicate_occurrences(
    rows: list[dict[str, Any]], fuzzy_threshold: int = 90,
) -> list[DedupPlan]:
    """Group source occurrences into deduplicated records.

    Exact DOI equality is authoritative. A DOI-missing occurrence may bridge
    into a DOI group only through the conservative fuzzy title/author/year
    comparison; two records that both carry *different* DOIs remain separate.
    """
    by_doi: dict[str, list[dict[str, Any]]] = defaultdict(list)
    no_doi: list[dict[str, Any]] = []
    for r in rows:
        if r.get("doi"):
            by_doi[r["doi"]].append(r)
        else:
            no_doi.append(r)

    doi_groups = [
        {
            "rows": members[:],
            "method": "doi" if len(members) > 1 else "singleton",
            "members": [
                {"source_occurrence_id": member["id"], "similarity": 1.0, "match_method": "doi"}
                for member in members
            ],
        }
        for members in by_doi.values()
    ]

    # 1) Merge no-DOI occurrences into an equivalent DOI group. A fuzzy bridge
    # is deliberately one-way: different DOI groups are never merged by title.
    unmatched_no_doi: list[dict[str, Any]] = []
    doi_groups_by_key: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for group in doi_groups:
        for member in group["rows"]:
            doi_groups_by_key[_fuzzy_key(member)].append(group)

    for row in no_doi:
        candidates = _doi_fuzzy_candidates(
            row, doi_groups_by_key[_fuzzy_key(row)], fuzzy_threshold,
        )
        if not candidates:
            unmatched_no_doi.append(row)
            continue
        best_similarity, best_group = max(candidates, key=lambda candidate: candidate[0])
        best_group["rows"].append(row)
        best_group["members"].append({
            "source_occurrence_id": row["id"],
            "similarity": best_similarity / 100.0,
            "match_method": "fuzzy_title_author_year",
        })
        best_group["method"] = "doi_fuzzy_title_author_year"

    plans: list[DedupPlan] = [
        DedupPlan(
            canonical_occurrence_id=max(group["rows"], key=_completeness_score)["id"],
            method=group["method"],
            members=group["members"],
        )
        for group in doi_groups
    ]

    # 2) Fuzzy groups across the remaining no-DOI occurrences.
    blocks: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for row in unmatched_no_doi:
        blocks[_fuzzy_key(row)].append(row)

    for _key, block in blocks.items():
        remaining = block[:]
        while remaining:
            seed = remaining.pop(0)
            seed_title = normalize_title(seed.get("title"))
            group_members = [{
                "source_occurrence_id": seed["id"], "similarity": 1.0, "match_method": "singleton",
            }]
            group_rows = [seed]
            still_remaining: list[dict[str, Any]] = []
            for other in remaining:
                sim = fuzz.token_set_ratio(seed_title, normalize_title(other.get("title")))
                if seed_title and normalize_title(other.get("title")) and sim >= fuzzy_threshold:
                    group_members.append({
                        "source_occurrence_id": other["id"],
                        "similarity": sim / 100.0,
                        "match_method": "fuzzy_title_author_year",
                    })
                    group_rows.append(other)
                else:
                    still_remaining.append(other)
            remaining = still_remaining
            canonical = max(group_rows, key=_completeness_score)
            method = "fuzzy_title_author_year" if len(group_rows) > 1 else "singleton"
            plans.append(
                DedupPlan(
                    canonical_occurrence_id=canonical["id"],
                    method=method,
                    members=group_members,
                )
            )

    return plans


def _fuzzy_key(row: dict[str, Any]) -> tuple[str, int | None]:
    return _author_lastname(row.get("authors_json")), row.get("year")


def _doi_fuzzy_candidates(
    row: dict[str, Any], groups: list[dict[str, Any]], fuzzy_threshold: int,
) -> list[tuple[float, dict[str, Any]]]:
    """Matching DOI groups for a no-DOI row, scored by their best title match."""
    row_title = normalize_title(row.get("title"))
    if not row_title:
        return []
    candidates: list[tuple[float, dict[str, Any]]] = []
    for group in groups:
        score = max(
            (
                fuzz.token_set_ratio(row_title, normalize_title(candidate.get("title")))
                for candidate in group["rows"]
                if normalize_title(candidate.get("title"))
            ),
            default=0.0,
        )
        if score >= fuzzy_threshold:
            candidates.append((score, group))
    return candidates


def plans_to_payload(plans: list[DedupPlan]) -> list[dict[str, Any]]:
    return [
        {
            "canonical_occurrence_id": p.canonical_occurrence_id,
            "method": p.method,
            "members": p.members,
        }
        for p in plans
    ]
