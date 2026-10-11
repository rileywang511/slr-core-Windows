"""PRISMA counts + diagram rendering.

Reads the review's CSVs; writes prisma.json
(canonical), prisma.mmd (Mermaid source), prisma.svg (only if `mmdc` is on PATH).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from slr import frames
from slr.audit import utc_now_iso


def compute_prisma(review_dir: Path) -> dict[str, Any]:
    identification = {r["source"]: r["n_returned"] for r in frames.source_run_summary(review_dir)}
    n_identified_total = sum(identification.values())

    n_source_occurrences = frames.active_provenance_count(review_dir)
    n_records = frames.record_count(review_dir)
    n_duplicates_removed = max(n_source_occurrences - n_records, 0)

    ta_rows = frames.latest_decisions(review_dir, "title_abstract")
    n_screened_ta = len(ta_rows)
    ta_excluded = [r for r in ta_rows if r["decision"] == "exclude"]
    ta_reasons = Counter(r["reason_code"] or "unspecified" for r in ta_excluded)

    ft_rows = frames.latest_decisions(review_dir, "full_text")
    n_assessed_ft = len(ft_rows)
    ft_excluded = [r for r in ft_rows if r["decision"] == "exclude"]
    ft_reasons = Counter(r["reason_code"] or "unspecified" for r in ft_excluded)
    n_included = sum(1 for r in ft_rows if r["decision"] == "include") if ft_rows else sum(
        1 for r in ta_rows if r["decision"] == "include"
    )

    n_ft_retrieved = frames.n_records_with_pdf(review_dir)
    n_ft_attempted = frames.n_records_attempted(review_dir)

    return {
        "generated_at": utc_now_iso(),
        "identification": {
            "per_source": identification,
            "n_total": n_identified_total,
            "n_source_occurrences": n_source_occurrences,
            "n_records_stored": n_records,
        },
        "deduplication": {
            "n_duplicates_removed": n_duplicates_removed,
            "n_after_dedup": n_records,
        },
        "screening_title_abstract": {
            "n_screened": n_screened_ta,
            "n_excluded": len(ta_excluded),
            "n_passed": n_screened_ta - len(ta_excluded),
            "exclusion_reasons": dict(ta_reasons),
        },
        "fulltext_retrieval": {
            "n_attempted": n_ft_attempted,
            "n_retrieved": n_ft_retrieved,
            "n_unavailable": max(n_ft_attempted - n_ft_retrieved, 0),
        },
        "screening_full_text": {
            "n_assessed": n_assessed_ft,
            "n_excluded": len(ft_excluded),
            "exclusion_reasons": dict(ft_reasons),
        },
        "inclusion": {
            "n_included": n_included,
        },
    }


def render_mermaid(data: dict[str, Any]) -> str:
    ident = data["identification"]
    dedup = data["deduplication"]
    ta = data["screening_title_abstract"]
    ft_get = data["fulltext_retrieval"]
    ft = data["screening_full_text"]
    inc = data["inclusion"]

    per_source_lines = "<br/>".join(f"{src}: {n}" for src, n in ident["per_source"].items()) or "—"

    return f"""flowchart TD
  ID["Records identified<br/>{per_source_lines}<br/><b>total = {ident['n_total']}</b>"]
  DUP["Duplicates removed<br/>n = {dedup['n_duplicates_removed']}"]
  DEDUP["Records after dedup<br/>n = {dedup['n_after_dedup']}"]
  TA["Title/abstract screened<br/>n = {ta['n_screened']}"]
  TAX["Excluded at TA<br/>n = {ta['n_excluded']}"]
  FT_GET["Full text retrieved<br/>n = {ft_get['n_retrieved']} / {ft_get['n_attempted']}"]
  FT["Full text assessed<br/>n = {ft['n_assessed']}"]
  FTX["Excluded at FT<br/>n = {ft['n_excluded']}"]
  INC["Included in synthesis<br/><b>n = {inc['n_included']}</b>"]
  ID --> DUP --> DEDUP --> TA
  TA --> TAX
  TA --> FT_GET --> FT
  FT --> FTX
  FT --> INC
"""


def _mermaid_command() -> list[str] | None:
    executable = shutil.which("mmdc")
    if not executable:
        return None
    if os.name == "nt" and Path(executable).suffix.lower() in (".cmd", ".bat", ".ps1"):
        # npm's Windows shim is a shell script. Call its JS entry point with
        # Node directly so review paths (including shell metacharacters) stay
        # ordinary arguments rather than being interpreted by cmd.exe.
        shim_dir = Path(executable).parent
        entry = shim_dir / "node_modules" / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
        if not entry.is_file():
            # Local npm installs put shims in node_modules/.bin.
            entry = shim_dir.parent / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
        node = shim_dir / "node.exe"
        node_executable = str(node) if node.is_file() else shutil.which("node")
        if entry.is_file() and node_executable:
            return [node_executable, str(entry)]
        return None
    return [executable]


def write_outputs(review_dir: Path) -> dict[str, str]:
    data = compute_prisma(review_dir)
    json_path = review_dir / "prisma.json"
    mmd_path = review_dir / "prisma.mmd"
    svg_path = review_dir / "prisma.svg"

    frames.atomic_write_text(json_path, json.dumps(data, indent=2))
    mmd = render_mermaid(data)
    frames.atomic_write_text(mmd_path, mmd)

    svg_rendered = False
    command = _mermaid_command()
    if command:
        try:
            subprocess.run(
                [*command, "-i", str(mmd_path), "-o", str(svg_path)],
                check=True,
                capture_output=True,
            )
            svg_rendered = True
        except (subprocess.CalledProcessError, OSError):
            svg_rendered = False

    return {
        "json": str(json_path),
        "mmd": str(mmd_path),
        "svg": str(svg_path) if svg_rendered else "",
    }
