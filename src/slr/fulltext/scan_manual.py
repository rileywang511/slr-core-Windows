"""Pick up PDFs the user has manually placed in the cache directory.

`slr screen --stage full_text` calls `scan_for_manual_pdfs()` at the start of
its run. For each file in pdfs/ whose name matches the record-ID filename
scheme:

  1. If we already have a successful `fetch_attempts.csv` row for that record,
     skip (avoid duplicating work / overwriting the auto-download record).
  2. Otherwise, compute the matching final record (by recomputing
     ``target_basename`` for every record and comparing), then insert a new attempt with
     source='manual', the file path, and the content sha256.

The result is a transparent ingest: the user never types a command; they just
save the file with the name we told them to use.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from slr.audit import utc_now_iso
from slr.fulltext.naming import is_target_filename, target_basename
from slr import frames


def scan_for_manual_pdfs(review_dir: Path, cache_dir: str) -> dict[str, int]:
    """Returns {'ingested': N, 'skipped_existing': M, 'unrecognised': K}."""
    abs_cache = review_dir / cache_dir
    if not abs_cache.exists():
        return {"ingested": 0, "skipped_existing": 0, "unrecognised": 0}

    # Build a map: target filename -> (record_id, has_pdf_already).
    record_index: dict[str, tuple[int, bool]] = {}
    with_pdf = set(frames.successful_pdf_per_record(review_dir))
    for record_id, row in frames.records_by_id(review_dir).items():
        name = target_basename(
            record_id=record_id,
            doi=row.get("doi"),
            title=row.get("title"),
            year=row.get("year"),
        )
        record_index[name] = (record_id, record_id in with_pdf)

    ingested = 0
    skipped_existing = 0
    unrecognised = 0

    for path in sorted(abs_cache.iterdir()):
        if not path.is_file():
            continue
        if not is_target_filename(path.name):
            continue
        match = record_index.get(path.name)
        if match is None:
            unrecognised += 1
            continue
        record_id, has_pdf = match
        if has_pdf:
            skipped_existing += 1
            continue

        data = path.read_bytes()
        if not data.startswith(b"%PDF-"):
            # Looks like our naming but isn't a real PDF — leave it for the
            # user to fix, don't crash.
            continue
        sha = hashlib.sha256(data).hexdigest()
        rel_path = Path(cache_dir) / path.name
        frames.append_fulltext_attempt(review_dir, {
            "record_id": record_id,
            "source": "manual",
            "bytes": len(data),
            "pdf_sha256": sha,
            "pdf_path": rel_path.as_posix(),
            "attempted_at": utc_now_iso(),
        })
        ingested += 1

    return {
        "ingested": ingested,
        "skipped_existing": skipped_existing,
        "unrecognised": unrecognised,
    }
