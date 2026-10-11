"""CSV/JSON persistence for a review directory.

Every pipeline stage reads and writes explicit, human-readable artifacts, so
all intermediate state is plain and inspectable:

  records.csv                 one sanitised, deduplicated paper per row
  evidence.jsonl              append-only source, warning, and dedup evidence
  screen_title_abstract.csv   title/abstract decisions (appended one at a time)
  screen_full_text.csv        full-text decisions (appended one at a time)
  fetch_attempts.csv          OA resolver attempts per final record
  fulltext_conversions.csv    PDF -> text conversions per final record
  _state.json                 per-stage {input_hash, output_hash, completed_at}

The functions here take a ``review_dir`` explicitly. This keeps file ownership
visible at every call site and avoids hiding review state behind an abstraction.

Values round-trip as strings in CSV; on read we coerce known integer/float
columns and treat empty cells as ``None`` to match the old DB's NULL semantics.
"""

from __future__ import annotations

import csv
import errno

try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None
import hashlib
import io
import json
import os
import sys
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from slr.audit import sha256_file, utc_now_iso
from slr.record import Record

# raw_json / prompt cells can be large; lift the default 128 KB field cap.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# ---- artifact filenames ----
RECORDS = "records.csv"
EVIDENCE = "evidence.jsonl"
# These names are read only to migrate review directories created before the
# evidence ledger. New reviews never create or update them.
_LEGACY_PROVENANCE = "provenance.csv"
_LEGACY_SOURCE_RUNS = "source_runs.csv"
_LEGACY_ACTIVE_SOURCE_RUNS = "active_source_runs.json"
FETCH_ATTEMPTS = "fetch_attempts.csv"
CONVERSIONS = "fulltext_conversions.csv"
STATE = "_state.json"


def screen_file(stage: str) -> str:
    return f"screen_{stage}.csv"


# A stage's outputs are invalid only when one of its upstream stages changed.
# This graph keeps the pipeline's dependencies explicit rather than relying on
# command ordering alone.
STAGE_DEPENDENTS: dict[str, tuple[str, ...]] = {
    "search": ("dedup",),
    "dedup": ("screen_title_abstract",),
    "screen_title_abstract": ("fetch",),
    "fetch": ("screen_full_text",),
    "screen_full_text": (),
}
_STAGE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "dedup": (RECORDS,),
    "screen_title_abstract": (screen_file("title_abstract"),),
    "fetch": (FETCH_ATTEMPTS, CONVERSIONS, "unresolved.csv", "reprints.csv"),
    "screen_full_text": (screen_file("full_text"), CONVERSIONS),
}


# ---- column schemas ----
RECORD_COLS = [
    "id", "doi", "title", "abstract", "authors_json", "year", "venue", "type",
    "url", "pmid", "pmcid", "dedup_method", "created_at",
]
DECISION_COLS = [
    "id", "record_id", "decision", "reason_code", "reason_text",
    "screener", "model_id", "prompt", "response", "input_tokens",
    "output_tokens", "latency_ms", "decided_at",
]
ATTEMPT_COLS = [
    "id", "record_id", "source", "url", "http_status", "bytes",
    "pdf_sha256", "pdf_path", "resolver_metadata_json", "attempted_at", "error",
]
CONVERSION_COLS = [
    "id", "record_id", "converter", "pdf_sha256", "marker_version",
    "config_hash", "output_format", "output_path", "pages", "used_llm",
    "used_ocr", "elapsed_ms", "converted_at", "error",
]

# Columns coerced to int / float on read (empty cell -> None either way).
_INT_COLS = {
    "id", "record_id", "source_run_id", "selected_occurrence_id",
    "year", "n_returned", "http_status", "bytes", "pages", "used_llm",
    "used_ocr", "elapsed_ms", "input_tokens", "output_tokens", "latency_ms",
}
_FLOAT_COLS = {"similarity"}


# ---------------------------------------------------------------------------
# low-level CSV helpers
# ---------------------------------------------------------------------------

def _coerce(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None or v == "":
            out[k] = None
        elif k in _INT_COLS:
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
        elif k in _FLOAT_COLS:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return 1 if v else 0
    return v


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = []
        for row in csv.DictReader(f):
            # Review directories created before records replaced clusters used
            # `cluster_id` in downstream evidence. Preserve that history while
            # presenting one unambiguous record_id to current code.
            if row.get("record_id") in (None, "") and row.get("cluster_id") not in (None, ""):
                row["record_id"] = row["cluster_id"]
            # The representative imported citation used to be labeled
            # `canonical_evidence_id`; the selected occurrence name makes its
            # role explicit while preserving existing review directories.
            if (row.get("selected_occurrence_id") in (None, "")
                    and row.get("canonical_evidence_id") not in (None, "")):
                row["selected_occurrence_id"] = row["canonical_evidence_id"]
            rows.append(_coerce(row))
        return rows


def _write(path: Path, cols: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Materialise once so the final ``os.replace`` is all-or-nothing. The
    # review directory is deliberately made of ordinary files, but ordinary
    # should not mean a power loss can leave a half-written CSV behind.
    contents = io.StringIO(newline="")
    w = csv.DictWriter(contents, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for row in rows:
        w.writerow({c: _cell(row.get(c)) for c in cols})
    _atomic_write_text(path, contents.getvalue())


def _atomic_write_text(path: Path, contents: str) -> None:
    """Durably replace a small review artifact without exposing partial data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        # Persist the rename itself on filesystems where directory fsync is
        # supported (not every platform exposes it, hence the best effort).
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, contents: str) -> None:
    """Write a textual artifact atomically for callers outside this module."""
    _atomic_write_text(path, contents)


def atomic_write_bytes(path: Path, contents: bytes) -> None:
    """Atomically replace a binary review artifact such as an acquired PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _append(path: Path, cols: list[str], row: dict[str, Any]) -> None:
    exists = path.exists()
    if exists:
        with path.open(newline="", encoding="utf-8-sig") as f:
            existing_cols = next(csv.reader(f), [])
        if existing_cols != cols:
            _write(path, cols, _read(path))
    if not exists:
        _write(path, cols, [row])
        return
    # One encoded write plus fsync keeps append-only audit rows durable while
    # avoiding an O(n^2) full-file rewrite during long screening runs. Commands
    # hold ``review_write_lock`` so simultaneous writers cannot interleave.
    contents = io.StringIO(newline="")
    csv.DictWriter(contents, fieldnames=cols, extrasaction="ignore").writerow(
        {c: _cell(row.get(c)) for c in cols}
    )
    with path.open("ab", buffering=0) as f:
        f.write(contents.getvalue().encode())
        f.flush()
        os.fsync(f.fileno())


def _next_id(rows: list[dict[str, Any]]) -> int:
    return (max((r["id"] for r in rows if r.get("id") is not None), default=0)) + 1


# ---------------------------------------------------------------------------
# init / state
# ---------------------------------------------------------------------------

def state_path(review_dir: Path) -> Path:
    return review_dir / STATE


def init(review_dir: Path) -> None:
    """Create the review directory and its state file if missing (idempotent)."""
    review_dir.mkdir(parents=True, exist_ok=True)
    p = state_path(review_dir)
    if not p.exists():
        _atomic_write_text(p, "{}")


@contextmanager
def review_write_lock(review_dir: Path):
    """Exclusively lock a review while a mutating command is running.

    The lock is advisory, which is appropriate for a local CLI: other ``slr``
    processes cooperate, while users can still inspect every artifact. It is
    intentionally a separate dotfile rather than state hidden in a service.
    """
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / ".slr.write.lock"
    with path.open("a+b") as f:
        # Windows locks a byte range. Ensure byte zero exists and always seek
        # to it before acquiring/releasing (append mode starts at EOF).
        if msvcrt is not None:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"\0")
                f.flush()
        f.seek(0)
        try:
            if msvcrt is not None:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise RuntimeError("review locking is unsupported on this platform")
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            raise RuntimeError(
                f"another slr command is already writing review {review_dir}"
            ) from exc
        try:
            yield
        finally:
            f.seek(0)
            if msvcrt is not None:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _load_state(review_dir: Path) -> dict[str, Any]:
    p = state_path(review_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig") or "{}")
    except json.JSONDecodeError:
        return {}


def artifact_hash(review_dir: Path, *relative_paths: str) -> str:
    """Stable SHA-256 fingerprint for named review artifacts (missing is explicit)."""
    entries = [
        (name, sha256_file(review_dir / name))
        for name in relative_paths
    ]
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def evidence_hash(review_dir: Path, *event_types: str) -> str:
    """Fingerprint selected ledger event types, not unrelated later stages.

    One ledger deliberately holds source and deduplication history. Stage hashes
    select only their direct inputs so re-running dedup does not invalidate
    screening merely because it appended another historical decision.
    """
    wanted = set(event_types)
    entries = [event for event in _events(review_dir)
               if not wanted or event.get("type") in wanted]
    payload = json.dumps(entries, default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def active_evidence_hash(review_dir: Path, *event_types: str) -> str:
    """Fingerprint the current source snapshot, excluding superseded history."""
    events = _events(review_dir)
    known = {int(event["id"]) for event in events
             if event.get("type") == "source_run" and event.get("id") is not None}
    snapshots = [event for event in events if event.get("type") == "active_snapshot"]
    active = ({int(i) for i in snapshots[-1].get("active_run_ids", [])} & known
              if snapshots else known)
    wanted = set(event_types)
    entries = []
    for event in events:
        if wanted and event.get("type") not in wanted:
            continue
        if event.get("type") in {"source_run", "occurrence", "warning"}:
            if event.get("source_run_id", event.get("id")) not in active:
                continue
        elif event.get("type") == "active_snapshot":
            if not snapshots or event is not snapshots[-1]:
                continue
        entries.append(event)
    payload = json.dumps(entries, default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tree_hash(path: Path) -> str:
    """Stable SHA-256 fingerprint for all files below ``path``."""
    entries = []
    if path.exists():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            entries.append((str(child.relative_to(path)), sha256_file(child)))
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def _descendants(stage: str, include_self: bool = False) -> list[str]:
    found: list[str] = [stage] if include_self else []
    pending = list(STAGE_DEPENDENTS.get(stage, ()))
    while pending:
        current = pending.pop(0)
        if current in found:
            continue
        found.append(current)
        pending.extend(STAGE_DEPENDENTS.get(current, ()))
    return found


def _invalidate(state: dict[str, Any], review_dir: Path, stages: Iterable[str]) -> list[str]:
    invalidated: list[str] = []
    for stage in stages:
        if stage in state:
            state.pop(stage)
            invalidated.append(stage)
        for artifact in _STAGE_ARTIFACTS.get(stage, ()):
            (review_dir / artifact).unlink(missing_ok=True)
    return invalidated


def invalidate_if_input_changed(review_dir: Path, stage: str, input_hash: str) -> list[str]:
    """Discard a completed stage and its dependents if its inputs no longer match.

    This is used before resumable commands read their prior output. It prevents
    stale decisions or acquisition evidence from being treated as a valid resume
    point after a protocol/configuration/input change.
    """
    state = _load_state(review_dir)
    previous = state.get(stage)
    if previous is None or previous.get("input_hash") == input_hash:
        return []
    invalidated = _invalidate(state, review_dir, _descendants(stage, include_self=True))
    _atomic_write_text(state_path(review_dir), json.dumps(state, indent=2))
    return invalidated


def set_stage(review_dir: Path, stage: str, input_hash: str, output_hash: str) -> list[str]:
    """Mark a stage complete and invalidate dependents when its result changed."""
    state = _load_state(review_dir)
    previous = state.get(stage)
    invalidated: list[str] = []
    if previous and (
        previous.get("input_hash") != input_hash
        or previous.get("output_hash") != output_hash
    ):
        invalidated = _invalidate(state, review_dir, _descendants(stage))
    state[stage] = {
        "input_hash": input_hash,
        "output_hash": output_hash,
        "completed_at": utc_now_iso(),
    }
    _atomic_write_text(state_path(review_dir), json.dumps(state, indent=2))
    return invalidated


def get_stage(review_dir: Path, stage: str) -> dict[str, Any] | None:
    entry = _load_state(review_dir).get(stage)
    if entry is None:
        return None
    return {"stage": stage, **entry}


# ---------------------------------------------------------------------------
# append-only evidence ledger
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """Make arbitrary source payload keys safe to sort and write as JSON.

    ``csv.DictReader`` represents values beyond the declared header as a
    ``None`` key. Source APIs can also use non-string mapping keys. JSON object
    keys must be strings, and sorting a mixed-key mapping raises ``TypeError``;
    retain those values under an explicit, reviewable label instead.
    """
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            base = "<unnamed column>" if key is None else str(key)
            name = base
            suffix = 2
            while name in safe:
                name = f"{base} ({suffix})"
                suffix += 1
            safe[name] = _json_safe(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    """Durably append one self-contained, human-readable evidence event."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(_json_safe(event), default=str, sort_keys=True, ensure_ascii=False) + "\n"
    with path.open("ab", buffering=0) as f:
        f.write(encoded.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def _legacy_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return value


def _migrate_legacy_evidence(review_dir: Path) -> None:
    """Convert old evidence files once, without silently discarding them.

    The files are deliberately left in place: a human can compare the migrated
    ledger with its source, while all current code subsequently reads the new
    ledger. New reviews never create the legacy paths.
    """
    destination = review_dir / EVIDENCE
    if destination.exists():
        return
    old_runs = _read(review_dir / _LEGACY_SOURCE_RUNS)
    old_occurrences = _read(review_dir / _LEGACY_PROVENANCE)
    if not old_runs and not old_occurrences:
        return

    events: list[dict[str, Any]] = [{
        "type": "migration", "recorded_at": utc_now_iso(),
        "from": [_LEGACY_SOURCE_RUNS, _LEGACY_PROVENANCE, _LEGACY_ACTIVE_SOURCE_RUNS],
    }]
    for row in old_runs:
        warnings = _legacy_json(row.get("translation_warnings_json")) or []
        events.append({
            "type": "source_run", "id": row.get("id"), "source": row.get("source"),
            "query_raw": row.get("query_raw"), "query_translated": row.get("query_translated"),
            "ran_at": row.get("ran_at") or utc_now_iso(), "tool_version": row.get("tool_version"),
            "n_returned": row.get("n_returned") or 0, "run_kind": _run_kind(row),
            "legacy_raw_response": {"path": row.get("raw_response_path"),
                                    "sha256": row.get("raw_response_sha256")},
        })
        for message in warnings:
            events.append(_warning_event(row.get("id"), message))
    for row in old_occurrences:
        normalized = {key: row.get(key) for key in (
            "doi", "title", "abstract", "year", "venue", "type", "url", "pmid", "pmcid"
        )}
        normalized["authors"] = _legacy_json(row.get("authors_json")) or []
        events.append({
            "type": "occurrence", "id": row.get("id"),
            "source_run_id": row.get("source_run_id"), "source": row.get("source"),
            "source_id": row.get("source_id"), "normalized": normalized,
            "raw": _legacy_json(row.get("raw_json")),
            "ingested_at": row.get("ingested_at") or utc_now_iso(),
        })

    # Preserve the latest legacy merge decisions so records.csv can still be
    # enriched with its selected occurrence immediately after migration.
    legacy_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in old_occurrences:
        if row.get("record_id") is not None:
            legacy_groups[int(row["record_id"])].append(row)
    if legacy_groups:
        events.append({"type": "dedup_run", "id": 1, "recorded_at": utc_now_iso(),
                       "migrated": True})
        records_by_legacy_id = {int(row["id"]): row for row in _read(review_dir / RECORDS)
                                if row.get("id") is not None}
        for record_id, members in sorted(legacy_groups.items()):
            selected = next((row for row in members
                             if row.get("selected_occurrence_id") == row.get("id")), members[0])
            events.append({
                "type": "dedup_result", "dedup_run_id": 1, "record_id": record_id,
                "canonical_occurrence_id": selected.get("id"),
                "method": records_by_legacy_id.get(record_id, {}).get("dedup_method") or "legacy",
                "members": [
                    {"source_occurrence_id": row.get("id"),
                     "match_method": row.get("match_method") or "legacy",
                     "similarity": row.get("similarity")}
                    for row in members
                ],
                "recorded_at": utc_now_iso(), "migrated": True,
            })

    known = {int(r["id"]) for r in old_runs if r.get("id") is not None}
    active = known
    legacy_snapshot = review_dir / _LEGACY_ACTIVE_SOURCE_RUNS
    if legacy_snapshot.exists():
        try:
            active = {int(i) for i in json.loads(legacy_snapshot.read_text(encoding="utf-8-sig")).get("active_run_ids", [])} & known
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    events.append({"type": "active_snapshot", "active_run_ids": sorted(active),
                   "recorded_at": utc_now_iso()})
    _atomic_write_text(destination, "".join(
        json.dumps(_json_safe(event), default=str, sort_keys=True, ensure_ascii=False) + "\n"
        for event in events
    ))


def _events(review_dir: Path) -> list[dict[str, Any]]:
    _migrate_legacy_evidence(review_dir)
    path = review_dir / EVIDENCE
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON in {EVIDENCE} line {line_number}: {exc.msg}") from exc
            if not isinstance(item, dict) or not item.get("type"):
                raise RuntimeError(f"invalid evidence event in {EVIDENCE} line {line_number}")
            events.append(item)
    return events


def _next_event_id(events: Iterable[dict[str, Any]], event_type: str) -> int:
    return max((int(e["id"]) for e in events if e.get("type") == event_type and e.get("id") is not None), default=0) + 1


def _warning_event(source_run_id: int | None, warning: Any) -> dict[str, Any]:
    if isinstance(warning, str):
        row_number, label, message, raw = None, None, warning, None
        code = "source_warning"
    elif isinstance(warning, dict):
        row_number = warning.get("row_number")
        label = warning.get("label")
        message = warning.get("message") or str(warning)
        raw = warning.get("raw")
        code = warning.get("code") or "record_parse_failed"
    else:
        row_number = getattr(warning, "row_number", None)
        label = getattr(warning, "label", None)
        message = getattr(warning, "message", str(warning))
        raw = getattr(warning, "raw", None)
        code = getattr(warning, "code", "record_parse_failed")
    return {
        "type": "warning", "source_run_id": source_run_id, "code": code,
        "message": message, "row_number": row_number, "row_label": label,
        "raw": raw, "recorded_at": utc_now_iso(),
    }


def append_evidence_error(
    review_dir: Path, operation: str, message: str, *, source: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an operational error alongside the source evidence it affected."""
    _append_jsonl(review_dir / EVIDENCE, {
        "type": "error", "operation": operation, "source": source,
        "message": message, "details": details or {}, "recorded_at": utc_now_iso(),
    })


def _run_kind(run: dict[str, Any]) -> str:
    if run.get("run_kind"):
        return str(run["run_kind"])
    return "import" if str(run.get("query_raw") or "").startswith("<IMPORT ") else "search"


def _source_runs(review_dir: Path) -> list[dict[str, Any]]:
    return [{key: value for key, value in event.items() if key != "type"}
            for event in _events(review_dir) if event.get("type") == "source_run"]


def append_source_run(
    review_dir: Path,
    source: str,
    query_raw: str,
    query_translated: str,
    warnings: Iterable[Any],
    tool_version: str,
    n_returned: int,
    run_kind: str = "search",
    input_sha256: str | None = None,
) -> int:
    """Record one source/import run and each warning as immutable ledger events."""
    events = _events(review_dir)
    run_id = _next_event_id(events, "source_run")
    _append_jsonl(review_dir / EVIDENCE, {
        "type": "source_run", "id": run_id, "source": source,
        "query_raw": query_raw, "query_translated": query_translated,
        "ran_at": utc_now_iso(), "tool_version": tool_version,
        "n_returned": n_returned, "run_kind": run_kind, "input_sha256": input_sha256,
    })
    for warning in warnings:
        _append_jsonl(review_dir / EVIDENCE, _warning_event(run_id, warning))
    return run_id


def active_source_run_ids(review_dir: Path) -> set[int]:
    """IDs in the latest published source snapshot (all runs until one exists)."""
    events = _events(review_dir)
    known = {int(event["id"]) for event in events
             if event.get("type") == "source_run" and event.get("id") is not None}
    snapshots = [event for event in events if event.get("type") == "active_snapshot"]
    if not snapshots:
        return known
    return {int(i) for i in snapshots[-1].get("active_run_ids", [])} & known


def _set_active_source_runs(review_dir: Path, run_ids: Iterable[int]) -> None:
    _append_jsonl(review_dir / EVIDENCE, {
        "type": "active_snapshot", "active_run_ids": sorted(set(run_ids)),
        "recorded_at": utc_now_iso(),
    })


def activate_search_runs(review_dir: Path, run_ids: Iterable[int]) -> None:
    """Publish one complete live-search snapshot without deleting past runs."""
    runs = _source_runs(review_dir)
    imports = {int(r["id"]) for r in runs if _run_kind(r) == "import"}
    completed = set(run_ids)
    known = {int(r["id"]) for r in runs}
    if not completed <= known:
        raise ValueError("cannot activate a source run that has not been recorded")
    _set_active_source_runs(review_dir, imports | completed)


def activate_import_run(review_dir: Path, run_id: int) -> None:
    """Add an import to the active view, replacing an identical import file."""
    runs = _source_runs(review_dir)
    current = next((run for run in runs if run.get("id") == run_id), None)
    if current is None:
        raise ValueError("cannot activate a source run that has not been recorded")
    active = active_source_run_ids(review_dir)
    for run in runs:
        if (run.get("id") in active and _run_kind(run) == "import"
                and run.get("source") == current.get("source")
                and run.get("input_sha256")
                and run.get("input_sha256") == current.get("input_sha256")):
            active.discard(int(run["id"]))
    active.add(run_id)
    _set_active_source_runs(review_dir, active)


def source_run_summary(review_dir: Path) -> list[dict[str, Any]]:
    """Current snapshot counts per source, retaining the latest run metadata."""
    totals: dict[str, dict[str, Any]] = {}
    active = active_source_run_ids(review_dir)
    for r in _source_runs(review_dir):
        if r.get("id") not in active:
            continue
        src = r["source"]
        cur = totals.get(src)
        if cur is None:
            totals[src] = {**r, "n_returned": r.get("n_returned") or 0}
        else:
            cur["n_returned"] = (cur.get("n_returned") or 0) + (r.get("n_returned") or 0)
            if (r.get("ran_at") or "", r.get("id") or 0) >= (cur.get("ran_at") or "", cur.get("id") or 0):
                latest_total = cur["n_returned"]
                cur.update(r)
                cur["n_returned"] = latest_total
    return list(totals.values())


# ---------------------------------------------------------------------------
# source occurrences and final records
# ---------------------------------------------------------------------------

def append_provenance(review_dir: Path, source_run_id: int, records: Iterable[Record]) -> int:
    """Append normalised source occurrences, de-duplicating only within a run."""
    events = _events(review_dir)
    occurrences = [event for event in events if event.get("type") == "occurrence"]
    seen = {(event.get("source_run_id"), event.get("source"), event.get("source_id"))
            for event in occurrences}
    next_id = _next_event_id(events, "occurrence")
    inserted = 0
    for record in records:
        key = (source_run_id, record.source, record.source_id)
        if key in seen:
            continue
        seen.add(key)
        _append_jsonl(review_dir / EVIDENCE, {
            "type": "occurrence", "id": next_id, "source_run_id": source_run_id,
            "source": record.source, "source_id": record.source_id,
            "normalized": {
                "doi": record.doi, "title": record.title, "abstract": record.abstract,
                "authors": [author.model_dump() for author in record.authors],
                "year": record.year, "venue": record.venue, "type": record.type,
                "url": record.url, "pmid": record.pmid, "pmcid": record.pmcid,
            },
            "raw": record.raw, "ingested_at": utc_now_iso(),
        })
        next_id += 1
        inserted += 1
    return inserted


def _dedup_assignments(events: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    runs = [event for event in events if event.get("type") == "dedup_run"]
    if not runs:
        return {}
    latest_id = runs[-1].get("id")
    assignments: dict[int, dict[str, Any]] = {}
    for event in events:
        if event.get("type") != "dedup_result" or event.get("dedup_run_id") != latest_id:
            continue
        for member in event.get("members", []):
            occurrence_id = member.get("source_occurrence_id")
            if occurrence_id is not None:
                assignments[int(occurrence_id)] = {
                    "record_id": event.get("record_id"),
                    "match_method": member.get("match_method", event.get("method")),
                    "similarity": member.get("similarity"),
                    "selected_occurrence_id": event.get("canonical_occurrence_id"),
                }
    return assignments


def _occurrence_row(event: dict[str, Any], assignments: dict[int, dict[str, Any]]) -> dict[str, Any]:
    normalized = event.get("normalized") or {}
    row = {
        "id": event.get("id"), "source_run_id": event.get("source_run_id"),
        "source": event.get("source"), "source_id": event.get("source_id"),
        "doi": normalized.get("doi"), "title": normalized.get("title"),
        "abstract": normalized.get("abstract"),
        "authors_json": json.dumps(normalized.get("authors") or [], ensure_ascii=False),
        "year": normalized.get("year"), "venue": normalized.get("venue"),
        "type": normalized.get("type"), "url": normalized.get("url"),
        "pmid": normalized.get("pmid"), "pmcid": normalized.get("pmcid"),
        "raw_json": json.dumps(event.get("raw"), default=str, ensure_ascii=False),
        "ingested_at": event.get("ingested_at"),
        "record_id": None, "match_method": None, "similarity": None,
        "selected_occurrence_id": None,
    }
    row.update(assignments.get(int(row["id"]), {}))
    return row


def read_provenance(review_dir: Path) -> list[dict[str, Any]]:
    """Compatibility view of occurrence evidence; persisted only in evidence.jsonl."""
    events = _events(review_dir)
    assignments = _dedup_assignments(events)
    return [_occurrence_row(event, assignments) for event in events if event.get("type") == "occurrence"]


def provenance_count(review_dir: Path) -> int:
    return len(read_provenance(review_dir))


def active_provenance(review_dir: Path) -> list[dict[str, Any]]:
    active = active_source_run_ids(review_dir)
    return [row for row in read_provenance(review_dir) if row.get("source_run_id") in active]


def active_provenance_count(review_dir: Path) -> int:
    return len(active_provenance(review_dir))


def read_records(review_dir: Path) -> list[dict[str, Any]]:
    return _read(review_dir / RECORDS)


def raw_json_by_final_record_id(review_dir: Path) -> dict[int, str | None]:
    """Return the selected source payload for each final record when available."""
    raw: dict[int, str | None] = {}
    for item in active_provenance(review_dir):
        record_id = item.get("record_id")
        if record_id is not None and item.get("selected_occurrence_id") == item.get("id"):
            raw[record_id] = item.get("raw_json")
    return raw


def record_count(review_dir: Path) -> int:
    return len(_read(review_dir / RECORDS))


def write_deduplicated_records(review_dir: Path, groups: list[dict[str, Any]]) -> None:
    """Rewrite final records and append the deduplication decision to the ledger.

    ``records.csv`` is the concise, user-facing list. Every selected occurrence
    and match decision is retained as a new immutable `dedup_result` event,
    rather than rewriting historical source evidence.
    """
    now = utc_now_iso()
    evidence_by_id = {r["id"]: r for r in active_provenance(review_dir)}
    previous_created_at = {
        _record_identity(record): record.get("created_at")
        for record in read_records(review_dir)
    }
    records: list[dict[str, Any]] = []
    dedup_run_id = _next_event_id(_events(review_dir), "dedup_run")
    _append_jsonl(review_dir / EVIDENCE, {
        "type": "dedup_run", "id": dedup_run_id,
        "active_run_ids": sorted(active_source_run_ids(review_dir)), "recorded_at": now,
    })
    for record_id, group in enumerate(groups, start=1):
        canonical_id = group["canonical_occurrence_id"]
        canonical = evidence_by_id[canonical_id]
        record = {
            "id": record_id,
            "doi": canonical.get("doi"),
            "title": canonical.get("title"),
            "abstract": canonical.get("abstract"),
            "authors_json": canonical.get("authors_json"),
            "year": canonical.get("year"),
            "venue": canonical.get("venue"),
            "type": canonical.get("type"),
            "url": canonical.get("url"),
            "pmid": canonical.get("pmid"),
            "pmcid": canonical.get("pmcid"),
            "dedup_method": group["method"],
        }
        record["created_at"] = previous_created_at.get(_record_identity(record), now)
        records.append(record)
        _append_jsonl(review_dir / EVIDENCE, {
            "type": "dedup_result", "dedup_run_id": dedup_run_id,
            "record_id": record_id, "canonical_occurrence_id": canonical_id,
            "method": group["method"], "members": group["members"],
            "recorded_at": now,
        })
    _write(review_dir / RECORDS, RECORD_COLS, records)


def _record_identity(record: dict[str, Any]) -> str:
    """Fields whose equality means a deduplicated record is unchanged."""
    return json.dumps(
        {key: record.get(key) for key in RECORD_COLS if key not in {"id", "created_at"}},
        sort_keys=True,
        default=str,
    )


def records_by_id(
    review_dir: Path, record_ids: Iterable[int] | None = None
) -> dict[int, dict[str, Any]]:
    """Final records keyed by stable ID, enriched with selected-source metadata."""
    wanted = set(record_ids) if record_ids is not None else None
    selected_occurrence = {
        item["record_id"]: item
        for item in active_provenance(review_dir)
        if item.get("record_id") is not None
        and item.get("selected_occurrence_id") == item.get("id")
    }
    out: dict[int, dict[str, Any]] = {}
    for record in read_records(review_dir):
        record_id = record["id"]
        if wanted is not None and record_id not in wanted:
            continue
        occurrence = selected_occurrence.get(record_id, {})
        out[record_id] = {
            **record,
            "record_id": record_id,
            "source_occurrence_id": occurrence.get("id"),
            "source": occurrence.get("source"),
            "source_id": occurrence.get("source_id"),
            "raw_json": occurrence.get("raw_json"),
        }
    return out


def final_records(review_dir: Path) -> list[dict[str, Any]]:
    """Final deduplicated records, ordered by their stable record ID."""
    records = records_by_id(review_dir)
    return [records[record_id] for record_id in sorted(records)]


def source_occurrence_counts(review_dir: Path) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for item in active_provenance(review_dir):
        if item.get("record_id"):
            counts[item["record_id"]] += 1
    return dict(counts)


def contributing_sources(
    review_dir: Path, record_ids: Iterable[int] | None = None
) -> dict[int, str]:
    """{record_id: 'source_a,source_b'} for the source occurrences behind a paper."""
    wanted = set(record_ids) if record_ids is not None else None
    per: dict[int, set[str]] = defaultdict(set)
    for item in active_provenance(review_dir):
        record_id = item.get("record_id")
        if record_id is None or (wanted is not None and record_id not in wanted):
            continue
        src = item.get("source")
        if src:
            per[record_id].add(src)
    return {record_id: ",".join(sorted(s)) for record_id, s in per.items()}


# ---------------------------------------------------------------------------
# screening decisions
# ---------------------------------------------------------------------------

def append_decision(review_dir: Path, stage: str, decision: dict[str, Any]) -> None:
    path = review_dir / screen_file(stage)
    row_id = _next_id(_read(path))
    _append(path, DECISION_COLS, {
        "id": row_id,
        "record_id": decision["record_id"],
        "decision": decision["decision"],
        "reason_code": decision.get("reason_code"),
        "reason_text": decision.get("reason_text"),
        "screener": decision["screener"],
        "model_id": decision.get("model_id"),
        "prompt": decision.get("prompt"),
        "response": decision.get("response"),
        "input_tokens": decision.get("input_tokens"),
        "output_tokens": decision.get("output_tokens"),
        "latency_ms": decision.get("latency_ms"),
        "decided_at": utc_now_iso(),
    })


def read_decisions(review_dir: Path, stage: str) -> list[dict[str, Any]]:
    return _read(review_dir / screen_file(stage))


def latest_decisions(review_dir: Path, stage: str) -> list[dict[str, Any]]:
    """Latest decision per final record (by decided_at, then ID)."""
    latest: dict[int, dict[str, Any]] = {}
    for d in read_decisions(review_dir, stage):
        record_id = d["record_id"]
        prev = latest.get(record_id)
        if prev is None or (
            (d.get("decided_at") or "", d.get("id") or 0)
            >= (prev.get("decided_at") or "", prev.get("id") or 0)
        ):
            latest[record_id] = d
    return list(latest.values())


def record_ids_with_decisions(review_dir: Path, stage: str) -> set[int]:
    return {d["record_id"] for d in read_decisions(review_dir, stage)}


def included_record_ids(review_dir: Path, stage: str) -> set[int]:
    return {d["record_id"] for d in latest_decisions(review_dir, stage)
            if d.get("decision") == "include"}


def decision_counts(review_dir: Path, stage: str) -> dict[str, int]:
    out = {"include": 0, "exclude": 0, "unsure": 0}
    for d in latest_decisions(review_dir, stage):
        dec = d.get("decision")
        if dec in out:
            out[dec] += 1
    return out


def record_ids_with_screener_error(review_dir: Path, stage: str) -> set[int]:
    return {d["record_id"] for d in latest_decisions(review_dir, stage)
            if d.get("reason_code") == "SCREENER_ERROR"}


def clear_stage_decisions(review_dir: Path, stage: str) -> int:
    path = review_dir / screen_file(stage)
    n = len(_read(path))
    if path.exists():
        path.unlink()
    return n


def clear_stage_decisions_for(review_dir: Path, stage: str, record_ids: set[int]) -> int:
    if not record_ids:
        return 0
    path = review_dir / screen_file(stage)
    rows = _read(path)
    kept = [r for r in rows if r["record_id"] not in record_ids]
    removed = len(rows) - len(kept)
    if removed:
        _write(path, DECISION_COLS, kept)
    return removed


def n_full_text_decisions(review_dir: Path) -> int:
    return len(read_decisions(review_dir, "full_text"))


# ---------------------------------------------------------------------------
# full-text attempts
# ---------------------------------------------------------------------------

def append_fulltext_attempt(review_dir: Path, attempt: dict[str, Any]) -> None:
    path = review_dir / FETCH_ATTEMPTS
    row_id = _next_id(_read(path))
    _append(path, ATTEMPT_COLS, {
        "id": row_id,
        "record_id": attempt.get("record_id"),
        "source": attempt.get("source"),
        "url": attempt.get("url"),
        "http_status": attempt.get("http_status"),
        "bytes": attempt.get("bytes"),
        "pdf_sha256": attempt.get("pdf_sha256"),
        "pdf_path": attempt.get("pdf_path"),
        "resolver_metadata_json": attempt.get("resolver_metadata_json"),
        "attempted_at": attempt.get("attempted_at") or utc_now_iso(),
        "error": attempt.get("error"),
    })


def _attempts(review_dir: Path) -> list[dict[str, Any]]:
    return _read(review_dir / FETCH_ATTEMPTS)


def successful_pdf_per_record(review_dir: Path) -> dict[int, str]:
    """{record_id: pdf_path} for records with a successful PDF (latest wins)."""
    out: dict[int, str] = {}
    ranks: dict[int, tuple[str, int]] = {}
    for a in _attempts(review_dir):
        if not a.get("pdf_sha256"):
            continue
        record_id = a["record_id"]
        rank = (a.get("attempted_at") or "", a.get("id") or 0)
        if record_id not in ranks or rank >= ranks[record_id]:
            ranks[record_id] = rank
            out[record_id] = a.get("pdf_path")
    return out


def successful_pdf_details(review_dir: Path) -> dict[int, dict[str, Any]]:
    """Latest successful attempt per record: {pdf_path, pdf_sha256, source}."""
    best: dict[int, dict[str, Any]] = {}
    rank: dict[int, tuple] = {}
    for a in _attempts(review_dir):
        if not a.get("pdf_sha256"):
            continue
        record_id = a["record_id"]
        r = (a.get("attempted_at") or "", a.get("id") or 0)
        if record_id not in rank or r >= rank[record_id]:
            rank[record_id] = r
            best[record_id] = {"record_id": record_id, "pdf_path": a.get("pdf_path"),
                         "pdf_sha256": a.get("pdf_sha256"), "source": a.get("source")}
    return best


def unresolved_record_ids(review_dir: Path) -> list[int]:
    """Records with >=1 attempt but no successful PDF, in record-ID order."""
    any_attempt: set[int] = set()
    success: set[int] = set()
    for a in _attempts(review_dir):
        any_attempt.add(a["record_id"])
        if a.get("pdf_sha256"):
            success.add(a["record_id"])
    return sorted(any_attempt - success)


def n_records_attempted(review_dir: Path) -> int:
    return len({a["record_id"] for a in _attempts(review_dir)})


def n_records_with_pdf(review_dir: Path) -> int:
    return len({a["record_id"] for a in _attempts(review_dir) if a.get("pdf_sha256")})


def attempts_by_record(review_dir: Path) -> dict[int, list[dict[str, Any]]]:
    """All attempts grouped by record, in insertion order."""
    per: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for a in _attempts(review_dir):
        per[a["record_id"]].append(a)
    return dict(per)


# ---------------------------------------------------------------------------
# full-text conversions
# ---------------------------------------------------------------------------

def append_conversion(review_dir: Path, conversion: dict[str, Any]) -> None:
    path = review_dir / CONVERSIONS
    row_id = _next_id(_read(path))
    row = {"id": row_id, **{c: conversion.get(c) for c in CONVERSION_COLS if c != "id"}}
    row.setdefault("converted_at", utc_now_iso())
    if not row.get("converted_at"):
        row["converted_at"] = utc_now_iso()
    _append(path, CONVERSION_COLS, row)


def _conversions(review_dir: Path) -> list[dict[str, Any]]:
    return _read(review_dir / CONVERSIONS)


def successful_conversions(review_dir: Path) -> dict[int, dict[str, Any]]:
    """Latest successful conversion per record: {output_path, converter, pages}."""
    best: dict[int, dict[str, Any]] = {}
    rank: dict[int, tuple] = {}
    for c in _conversions(review_dir):
        if c.get("error") is not None or not c.get("output_path"):
            continue
        record_id = c["record_id"]
        r = (c.get("converted_at") or "", c.get("id") or 0)
        if record_id not in rank or r >= rank[record_id]:
            rank[record_id] = r
            best[record_id] = c
    return best


def matching_conversion(
    review_dir: Path, record_id: int, converter: str, pdf_sha256: str, config_hash: str
) -> dict[str, Any] | None:
    """Latest usable conversion evidence matching this exact input and engine config."""
    matches = [
        row for row in _conversions(review_dir)
        if row.get("record_id") == record_id
        and row.get("converter") == converter
        and row.get("pdf_sha256") == pdf_sha256
        and row.get("config_hash") == config_hash
        and not row.get("error")
        and row.get("output_path")
    ]
    return max(matches, key=lambda row: (row.get("converted_at") or "", row.get("id") or 0), default=None)


def record_ids_with_conversion_error(review_dir: Path) -> set[int]:
    ok: set[int] = set()
    errored: set[int] = set()
    for c in _conversions(review_dir):
        record_id = c["record_id"]
        if c.get("error") is None and c.get("output_path"):
            ok.add(record_id)
        if c.get("error") is not None:
            errored.add(record_id)
    return errored - ok


def clear_errored_conversions(review_dir: Path, record_ids: set[int] | None = None) -> int:
    path = review_dir / CONVERSIONS
    rows = _conversions(review_dir)
    if not rows:
        return 0

    def is_errored_target(c: dict[str, Any]) -> bool:
        if c.get("error") is None:
            return False
        return record_ids is None or c["record_id"] in record_ids

    kept = [c for c in rows if not is_errored_target(c)]
    removed = len(rows) - len(kept)
    if removed:
        _write(path, CONVERSION_COLS, kept)
    return removed
