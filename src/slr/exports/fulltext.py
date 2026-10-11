"""CSV of the studies that passed FULL-TEXT screening, enriched with the PDF and
markdown paths, the screening reasoning, and provenance — a reviewer-facing
"included studies" report (distinct from the reference-manager exports)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from slr import frames

COLUMNS = [
    "record_id",
    # bibliographic
    "title", "authors", "year", "venue", "doi",
    # provenance: selected source occurrence + which databases found it
    "source", "source_id", "url", "pmid", "pmcid", "contributing_sources",
    # full-text screening decision + LLM reasoning
    "ft_decision", "ft_reason_code", "ft_reason_text", "ft_screener", "ft_decided_at",
    # title/abstract decision (upstream provenance)
    "ta_decision", "ta_reason_code", "ta_reason_text", "ta_screener",
    # acquired files + how they were produced
    "pdf_path", "pdf_sha256", "pdf_resolver",
    "markdown_path", "converter", "pages",
    # full abstract last (long)
    "abstract",
]


def _authors(authors_json: str | None) -> str:
    try:
        authors = json.loads(authors_json or "[]")
    except json.JSONDecodeError:
        return ""
    return "; ".join(
        f"{a.get('family', '')}, {a.get('given', '')}".strip(", ") for a in authors
    )


def export_fulltext_included(review_dir: Path, out_path: Path) -> int:
    """Write the included full-text-passed records to ``out_path``."""
    ft = {d["record_id"]: d for d in frames.latest_decisions(review_dir, "full_text")}
    ta = {d["record_id"]: d for d in frames.latest_decisions(review_dir, "title_abstract")}
    included = sorted(record_id for record_id, decision in ft.items() if decision.get("decision") == "include")

    records = _records_for(review_dir, included)
    sources = _contributing_sources(review_dir, included)
    pdfs = _successful_pdfs(review_dir)
    mds = _conversions(review_dir)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for record_id in included:
            rec = records.get(record_id, {})
            ftd = ft.get(record_id, {})
            tad = ta.get(record_id, {})
            pdf = pdfs.get(record_id, {})
            md = mds.get(record_id, {})
            w.writerow({
                "record_id": record_id,
                "title": rec.get("title", ""),
                "authors": _authors(rec.get("authors_json")),
                "year": rec.get("year", ""),
                "venue": rec.get("venue", ""),
                "doi": rec.get("doi", ""),
                "source": rec.get("source", ""),
                "source_id": rec.get("source_id", ""),
                "url": rec.get("url", ""),
                "pmid": rec.get("pmid", ""),
                "pmcid": rec.get("pmcid", ""),
                "contributing_sources": sources.get(record_id, ""),
                "ft_decision": ftd.get("decision", ""),
                "ft_reason_code": ftd.get("reason_code", ""),
                "ft_reason_text": ftd.get("reason_text", ""),
                "ft_screener": ftd.get("screener", ""),
                "ft_decided_at": ftd.get("decided_at", ""),
                "ta_decision": tad.get("decision", ""),
                "ta_reason_code": tad.get("reason_code", ""),
                "ta_reason_text": tad.get("reason_text", ""),
                "ta_screener": tad.get("screener", ""),
                "pdf_path": pdf.get("pdf_path", ""),
                "pdf_sha256": pdf.get("pdf_sha256", ""),
                "pdf_resolver": pdf.get("source", ""),
                "markdown_path": md.get("output_path", ""),
                "converter": md.get("converter", ""),
                "pages": md.get("pages", "") if md.get("pages") is not None else "",
                "abstract": rec.get("abstract", ""),
            })
    return len(included)


def _records_for(review_dir: Path, record_ids: list[int]) -> dict[int, dict]:
    if not record_ids:
        return {}
    return frames.records_by_id(review_dir, record_ids)


def _contributing_sources(review_dir: Path, record_ids: list[int]) -> dict[int, str]:
    if not record_ids:
        return {}
    return frames.contributing_sources(review_dir, record_ids)


def _successful_pdfs(review_dir: Path) -> dict[int, dict]:
    """Latest successful PDF attempt per record (path, SHA-256, resolver)."""
    return frames.successful_pdf_details(review_dir)


def _conversions(review_dir: Path) -> dict[int, dict]:
    """Latest successful conversion per record (markdown path, converter, pages)."""
    return frames.successful_conversions(review_dir)
