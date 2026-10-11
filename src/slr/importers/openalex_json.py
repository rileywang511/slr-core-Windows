"""OpenAlex JSON / JSONL importer.

Accepts three shapes:
  1. JSONL — one OpenAlex work object per line (`{"id":"https://openalex.org/W...", ...}`)
  2. JSON  — a single list of work objects:  `[{...}, {...}]`
  3. JSON  — a full OpenAlex API response:   `{"results": [{...}], "meta": {...}}`

Each work object is the exact API payload, so we reuse `OpenAlexSource.normalize()`
for parity with live searches — same field-mapping, same abstract reconstruction.
"""

from __future__ import annotations

import json
from pathlib import Path

from slr.importers.base import ImportResult, ImportWarning, Importer, synth_source_id
from slr.record import Record
from slr.sources.openalex import OpenAlexSource


class OpenAlexJsonImporter(Importer):
    name = "openalex_json"
    source_label = "openalex"  # bucket with the CSV importer in PRISMA

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        # JSON files have no CSV header; detection is done by extension + content
        return path.suffix.lower() in (".json", ".jsonl", ".ndjson")

    def parse(self, path: Path) -> ImportResult:
        works = list(self._iter_works(path))
        # Use a stub source (no HTTP) just for its normalize() method
        normalizer = OpenAlexSource.__new__(OpenAlexSource)
        normalizer.contact_email = None
        normalizer.http = None
        out: list[Record] = []
        warnings: list[ImportWarning] = []
        label = path.name
        for i, work in enumerate(works):
            try:
                rec = normalizer.normalize(work)
                if not rec.source_id:
                    rec.source_id = synth_source_id(label, i, rec)
                out.append(rec)
            except Exception as exc:
                warnings.append(ImportWarning(i, str(exc), work, label="entry"))
        return ImportResult(records=out, n_rows_read=len(works),
                            n_records_parsed=len(out), warnings=warnings)

    def _iter_works(self, path: Path):
        text = path.read_text(encoding="utf-8-sig")
        stripped = text.strip()
        if not stripped:
            return
        # JSONL — one object per line
        if path.suffix.lower() in (".jsonl", ".ndjson") or self._looks_like_jsonl(stripped):
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
            return
        # Full document
        doc = json.loads(stripped)
        if isinstance(doc, list):
            yield from doc
        elif isinstance(doc, dict):
            if "results" in doc and isinstance(doc["results"], list):
                yield from doc["results"]
            elif "id" in doc:
                # Single work object
                yield doc
            else:
                raise RuntimeError(f"unrecognised JSON shape in {path.name}: top-level keys = {list(doc)[:5]}")
        else:
            raise RuntimeError(f"unrecognised JSON top-level type: {type(doc).__name__}")

    @staticmethod
    def _looks_like_jsonl(s: str) -> bool:
        # First non-empty line is an object and there are more lines: JSONL.
        lines = [ln for ln in s.splitlines() if ln.strip()]
        if len(lines) < 2:
            return False
        first = lines[0].lstrip()
        return first.startswith("{") and first.rstrip().endswith("}")
