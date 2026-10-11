from __future__ import annotations

import json as jsonlib
import os
import sys
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer

from slr.audit import AuditLog, sha256_file, sha256_str
from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.exports import EXPORTERS, export_fulltext_included
from slr.importers import REGISTRY as IMPORT_REGISTRY, detect_format, get_importer
from slr.fulltext.acquire import Acquirer
from slr.fulltext.reprints import write_reprint_csv
from slr.fulltext.unresolved import write_unresolved_csv
from slr.llm.base import ProviderSpec
from slr.config import EXAMPLE_CONFIG, load_config_for_review
from slr.prisma import write_outputs as write_prisma_outputs
from slr.protocol import EXAMPLE_PROTOCOL, ScreenStage, load_protocol
from slr.query import parse as parse_query
from slr.query.assist import freetext_to_query
from slr.screen.runner import build_screener, run_screening
from slr.sources import get_source
from slr import frames
from slr.version import __version__

app = typer.Typer(
    help="Reproducible PRISMA-compliant systematic literature review CLI.",
    no_args_is_help=True,
)

# Subcommand group for LLM-related utilities (currently just `test`).
llm_app = typer.Typer(help="LLM provider utilities (test connectivity, etc.).",
                     no_args_is_help=True)
app.add_typer(llm_app, name="llm")


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        sys.stdout.write(jsonlib.dumps(payload, indent=2, default=str) + "\n")
    else:
        for k, v in payload.items():
            sys.stdout.write(f"{k}: {v}\n")


def _err(msg: str, code: int = 1, as_json: bool = False) -> "typer.Exit":
    if as_json:
        sys.stderr.write(jsonlib.dumps({"error": msg, "exit_code": code}) + "\n")
    else:
        sys.stderr.write(f"error: {msg}\n")
    return typer.Exit(code=code)


def _exclusive_review_write(command):
    """Keep each mutating CLI command the sole writer for its review."""
    @wraps(command)
    def wrapped(review_dir: Path, *args: Any, **kwargs: Any):
        with frames.review_write_lock(review_dir):
            return command(review_dir, *args, **kwargs)
    return wrapped


@app.callback()
def _root() -> None:
    """slr — reproducible systematic literature review CLI."""


@app.command("version")
def version_cmd() -> None:
    """Print tool version."""
    sys.stdout.write(__version__ + "\n")


@app.command("init")
@_exclusive_review_write
def init_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    json_out: Annotated[bool, typer.Option("--json", help="emit JSON")] = False,
    force: Annotated[bool, typer.Option("--force", help="overwrite existing protocol.yaml / config.yaml")] = False,
) -> None:
    """Scaffold a review directory with example protocol.yaml + config.yaml."""
    review_dir.mkdir(parents=True, exist_ok=True)
    proto = review_dir / "protocol.yaml"
    config = review_dir / "config.yaml"
    if proto.exists() and not force:
        raise _err(f"protocol.yaml already exists at {proto}; pass --force to overwrite",
                   as_json=json_out)
    frames.atomic_write_text(proto, EXAMPLE_PROTOCOL)
    # config.yaml is optional, but we scaffold an example for new reviews.
    if not config.exists() or force:
        frames.atomic_write_text(config, EXAMPLE_CONFIG)
    frames.init(review_dir)
    AuditLog(review_dir).append("init", args={"review_dir": str(review_dir)})
    _emit({
        "review_dir": str(review_dir),
        "protocol": str(proto),
        "config": str(config),
        "state": str(frames.state_path(review_dir)),
    }, json_out)


def _make_progress_fn(source_name: str, json_out: bool, quiet: bool):
    """Build a per-source progress callback that writes to stderr.

    Always emits to stderr (so --json on stdout stays clean). Disabled if
    --quiet or if stderr isn't a TTY (e.g. piped to a file) — falls back to
    a newline-per-page log in that case so progress is still visible.
    """
    if quiet:
        return None
    is_tty = sys.stderr.isatty()
    state = {"last_len": 0}

    def _write(line: str, *, force_newline: bool = False) -> None:
        if is_tty and not force_newline:
            pad = max(0, state["last_len"] - len(line))
            sys.stderr.write("\r" + line + " " * pad)
            sys.stderr.flush()
            state["last_len"] = len(line)
        else:
            # break out of the in-place line for important events
            if is_tty and state["last_len"]:
                sys.stderr.write("\n")
                state["last_len"] = 0
            sys.stderr.write(line + "\n")

    def fn(event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "retry":
            reason = event.get("reason") or "transient error"
            delay = event.get("delay") or 0.0
            attempt = (event.get("attempt") or 0) + 1
            _write(f"  {source_name}: retry #{attempt} after {delay:.1f}s — {reason}",
                   force_newline=True)
            return
        if kind == "give_up":
            _write(f"  {source_name}: gave up after retries — {event.get('error')}",
                   force_newline=True)
            return

        n = event.get("n_so_far") or 0
        total = event.get("n_total")
        limit_v = event.get("limit")
        phase = event.get("phase")
        target = limit_v if limit_v is not None else total
        if limit_v is not None:
            n = min(n, limit_v)
        if target:
            line = f"  {source_name}: {n}/{target}"
        else:
            line = f"  {source_name}: {n} fetched"
        if phase:
            line += f" [{phase}]"
        line += f" (page {event.get('page')})"
        _write(line)

    return fn


def _progress_done(source_name: str, n: int, quiet: bool) -> None:
    if quiet:
        return
    if sys.stderr.isatty():
        sys.stderr.write(f"\r  {source_name}: done — {n} records           \n")
    else:
        sys.stderr.write(f"  {source_name}: done — {n} records\n")
    sys.stderr.flush()


def _make_screen_progress(stage: str, total: int, quiet: bool,
                          initial_counts: dict[str, int] | None = None):
    """Per-record progress for screening with a running include/exclude/unsure tally.

    `initial_counts` seeds the tally from a prior partial run so resumed
    screening reports cumulative totals on the live progress line.

    Event kinds:
      - "decided": {decision, record_id, idx}
      - "error":   {error, record_id, idx}
    """
    if quiet:
        return None
    is_tty = sys.stderr.isatty()
    seed = initial_counts or {}
    state = {
        "last_len": 0,
        "include": seed.get("include", 0),
        "exclude": seed.get("exclude", 0),
        "unsure": seed.get("unsure", 0),
        "errors": 0,
    }
    # Throttle non-TTY output so a fast rules-based screener doesn't flood stderr.
    throttle = 1 if is_tty else max(1, total // 50 or 1)

    def _write(line: str, *, force_newline: bool = False) -> None:
        if is_tty and not force_newline:
            pad = max(0, state["last_len"] - len(line))
            sys.stderr.write("\r" + line + " " * pad)
            sys.stderr.flush()
            state["last_len"] = len(line)
        else:
            if is_tty and state["last_len"]:
                sys.stderr.write("\n")
                state["last_len"] = 0
            sys.stderr.write(line + "\n")
            sys.stderr.flush()

    def _tally_line(idx: int) -> str:
        n_err = state["errors"]
        err_part = f" | err {n_err}" if n_err else ""
        return (f"  {stage}: {idx}/{total} "
                f"[include {state['include']} | exclude {state['exclude']} "
                f"| unsure {state['unsure']}{err_part}]")

    def fn(event: dict[str, Any]) -> None:
        kind = event.get("kind")
        idx = event.get("idx") or 0
        if kind == "decided":
            decision = event.get("decision", "unsure")
            if decision in state:
                state[decision] += 1
            # Always print on the final item; otherwise throttle.
            if idx == total or idx % throttle == 0:
                _write(_tally_line(idx))
            return
        if kind == "error":
            state["errors"] += 1
            record_id = event.get("record_id")
            err = event.get("error")
            _write(f"  {stage}: record {record_id} failed — {err}", force_newline=True)
            return
        if kind == "retry":
            record_id = event.get("record_id")
            reason = event.get("reason")
            delay = event.get("delay") or 0.0
            attempt = (event.get("attempt") or 0) + 1
            _write(f"  {stage}: record {record_id} retry #{attempt} after {delay:.1f}s — {reason}",
                   force_newline=True)
            return

    return fn


def _screen_done(stage: str, counts: dict[str, int], n_errors: int, quiet: bool) -> None:
    if quiet:
        return
    summary = (f"  {stage}: done — include {counts['include']} | "
               f"exclude {counts['exclude']} | unsure {counts['unsure']}")
    if n_errors:
        summary += f" | err {n_errors}"
    if sys.stderr.isatty():
        sys.stderr.write("\r" + summary + " " * 20 + "\n")
    else:
        sys.stderr.write(summary + "\n")
    sys.stderr.flush()


def _make_fetch_progress(total: int, quiet: bool):
    """Per-record progress for `slr fetch`.

    Event kinds:
      - record_done: {idx, total, status: downloaded|missing, via: <resolver>|None,
                      attempts_for_record}
    """
    if quiet:
        return None
    is_tty = sys.stderr.isatty()
    throttle = 1 if is_tty else max(1, total // 50 or 1)
    state = {"last_len": 0, "downloaded": 0, "missing": 0}

    def _write(line: str, *, force_newline: bool = False) -> None:
        if is_tty and not force_newline:
            pad = max(0, state["last_len"] - len(line))
            sys.stderr.write("\r" + line + " " * pad)
            sys.stderr.flush()
            state["last_len"] = len(line)
        else:
            if is_tty and state["last_len"]:
                sys.stderr.write("\n")
                state["last_len"] = 0
            sys.stderr.write(line + "\n")
            sys.stderr.flush()

    def fn(event: dict[str, Any]) -> None:
        if event.get("kind") != "record_done":
            return
        status = event.get("status", "missing")
        state[status] = state.get(status, 0) + 1
        idx = event.get("idx") or 0
        via = event.get("via") or "(all resolvers exhausted)"
        if idx == total or idx % throttle == 0:
            line = (f"  fetch: {idx}/{total} "
                    f"[downloaded {state['downloaded']} | missing {state['missing']}] "
                    f"via {via}")
            _write(line)

    return fn


def _fetch_done(downloaded: int, missing: int, n_resolver_calls: int, quiet: bool) -> None:
    if quiet:
        return
    if sys.stderr.isatty():
        # Clear any trailing in-place line.
        sys.stderr.write("\r" + " " * 80 + "\r")
    sys.stderr.write(
        f"  fetch: done — downloaded {downloaded} | missing {missing}"
        f" ({n_resolver_calls} resolver calls)\n"
    )
    sys.stderr.flush()


def _format_status_table(rows: list[tuple[str, int | str]]) -> str:
    label_w = max(len(r[0]) for r in rows)
    val_w = max(len(str(r[1])) for r in rows)
    bar = "─" * (label_w + 2) + "┬" + "─" * (val_w + 2)
    top = "┌" + bar + "┐"
    bot = "└" + "─" * (label_w + 2) + "┴" + "─" * (val_w + 2) + "┘"
    body = "\n".join(
        f"│ {label:<{label_w}} │ {str(val):>{val_w}} │"
        for label, val in rows
    )
    return f"{top}\n{body}\n{bot}"


@app.command("search")
@_exclusive_review_write
def search_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    limit: Annotated[int | None, typer.Option(help="cap results per source (testing)")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="suppress progress output")] = False,
) -> None:
    """Run all configured source queries and write their results."""
    proto = load_protocol(review_dir / "protocol.yaml")
    cfg = load_config_for_review(review_dir)
    frames.init(review_dir)
    audit = AuditLog(review_dir)

    expr = parse_query(proto.query)
    summary: list[dict[str, Any]] = []
    completed_run_ids: list[int] = []
    for source_name in proto.sources:
        try:
            cls = get_source(source_name)
        except KeyError as e:
            raise _err(str(e), as_json=json_out)
        try:
            source = cls(
                contact_email=cfg.search.contact_email,
                http_config=cfg.search.http,
            )
        except RuntimeError as exc:
            raise _err(f"{source_name}: {exc}", as_json=json_out)
        date_from = proto.date_range.from_.isoformat() if proto.date_range.from_ else None
        date_to = proto.date_range.to.isoformat() if proto.date_range.to else None
        if not quiet:
            sys.stderr.write(f"[{source_name}] starting search\n")
            sys.stderr.flush()
        progress_fn = _make_progress_fn(source_name, json_out, quiet)
        try:
            result, records = source.search_and_normalize(
                expr, date_from=date_from, date_to=date_to, limit=limit,
                progress=progress_fn,
            )
            _progress_done(source_name, len(result.raw_records), quiet)
        except Exception as exc:
            if not quiet and sys.stderr.isatty():
                sys.stderr.write("\n")
            frames.append_evidence_error(
                review_dir, "search", str(exc), source=source_name,
                details={"query": proto.query.strip()},
            )
            audit.append("search", args={"source": source_name}, status="error",
                         details={"error": str(exc)})
            raise _err(f"{source_name} search failed: {exc}", as_json=json_out)

        for warning in result.warnings:
            if warning.startswith("EFetch failed"):
                sys.stderr.write(f"warning: {source_name}: {warning}\n")
                sys.stderr.flush()

        run_id = frames.append_source_run(
            review_dir,
            source=source_name,
            query_raw=proto.query.strip(),
            query_translated=result.query_translated,
            warnings=result.warnings,
            tool_version=__version__,
            n_returned=len(result.raw_records),
        )
        inserted = frames.append_provenance(review_dir, run_id, records)
        completed_run_ids.append(run_id)
        audit.append(
            "search",
            args={"source": source_name, "limit": limit},
            input_hash=sha256_str(proto.query),
            details={"n_returned": len(result.raw_records), "n_inserted": inserted,
                     "warnings": result.warnings},
        )
        summary.append({"source": source_name, "n_returned": len(result.raw_records),
                        "n_inserted": inserted, "warnings": result.warnings})

    # Do not activate a partial search: if any source above fails, its new raw
    # evidence remains immutable history but the previously active snapshot is
    # still the coherent input for deduplication and PRISMA.
    frames.activate_search_runs(review_dir, completed_run_ids)
    frames.set_stage(
        review_dir,
        "search",
        proto.hash(),
        frames.active_evidence_hash(
            review_dir, "source_run", "occurrence", "active_snapshot"
        ),
    )
    _emit({"review_dir": str(review_dir), "sources": summary, "next": "slr dedup"}, json_out)


@app.command("import")
@_exclusive_review_write
def import_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    file: Annotated[Path, typer.Argument(help="path to CSV / RIS export from a platform")],
    format: Annotated[
        str,
        typer.Option(help=f"auto|{'|'.join(IMPORT_REGISTRY.keys())}", show_default=True),
    ] = "auto",
    source_name: Annotated[
        str | None,
        typer.Option("--source", help="override the standardized source name for the imported records"),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Ingest records from a CSV (Scopus, WoS, PubMed, Europe PMC, generic) or RIS export."""
    if not file.exists():
        raise _err(f"file not found: {file}", as_json=json_out)
    if format == "auto":
        try:
            format = detect_format(file)
        except (RuntimeError, NotImplementedError) as exc:
            raise _err(str(exc), as_json=json_out)
    try:
        cls = get_importer(format)
    except KeyError as exc:
        raise _err(str(exc), as_json=json_out)

    frames.init(review_dir)
    importer = cls()
    try:
        result = importer.parse(file)
    except Exception as exc:
        frames.append_evidence_error(
            review_dir, "import", str(exc), source=source_name or cls.get_source_label(),
            details={"file": str(file), "format": format},
        )
        AuditLog(review_dir).append(
            "import", args={"file": str(file), "format": format}, status="error",
            details={"error": str(exc), "evidence": frames.EVIDENCE},
        )
        raise _err(f"{format} import failed: {exc}", as_json=json_out)

    # Optionally relabel the source so e.g. an exported-Scopus + live-Scopus
    # path both bucket under 'scopus' for dedup/identification.
    if source_name:
        for r in result.records:
            r.source = source_name

    src_label = source_name or cls.get_source_label()
    input_sha256 = sha256_file(file)
    run_id = frames.append_source_run(
        review_dir,
        source=src_label,
        query_raw=f"<IMPORT FROM {file.name}>",
        query_translated=f"importer={format}; file={file}",
        warnings=result.warnings,
        tool_version=__version__,
        n_returned=result.n_rows_read,
        run_kind="import",
        input_sha256=input_sha256,
    )
    n_inserted = frames.append_provenance(review_dir, run_id, result.records)
    frames.activate_import_run(review_dir, run_id)
    AuditLog(review_dir).append(
        "import",
        args={"file": str(file), "format": format, "source": src_label},
        input_hash=input_sha256,
        details={
            "n_rows_read": result.n_rows_read,
            "n_records_parsed": result.n_records_parsed,
            "n_inserted": n_inserted,
            "n_warnings": len(result.warnings),
            "warnings_recorded_in": frames.EVIDENCE,
        },
    )
    # Mark search stage complete so dedup will run
    frames.set_stage(
        review_dir,
        "search",
        input_sha256 or sha256_str(""),
        frames.active_evidence_hash(
            review_dir, "source_run", "occurrence", "active_snapshot"
        ),
    )
    _emit(
        {
            "file": str(file),
            "format": format,
            "source": src_label,
            "n_rows_read": result.n_rows_read,
            "n_records_parsed": result.n_records_parsed,
            "n_inserted": n_inserted,
            "n_warnings": len(result.warnings),
            "next": "slr dedup",
        },
        json_out,
    )


@app.command("query-assist")
def query_assist_cmd(
    review_dir: Annotated[Path, typer.Argument(help="review directory with config.yaml")],
    description: Annotated[str, typer.Argument(help="plain-English description of what to find")],
    stage: Annotated[str, typer.Option(help="which stage's LLM config to use: title_abstract or full_text")] = "title_abstract",
    write: Annotated[bool, typer.Option("--write", help="write the generated query into protocol.yaml")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Turn free text into a WoS-style boolean query using the configured LLM.

    The result is validated by the query parser before it's returned; `--write`
    saves it into protocol.yaml (only if it parses cleanly).
    """
    if stage not in ("title_abstract", "full_text"):
        raise _err(f"invalid stage: {stage}", as_json=json_out)
    cfg = load_config_for_review(review_dir)
    llm_cfg = cfg.screen.title_abstract if stage == "title_abstract" else cfg.screen.full_text
    if not llm_cfg.provider or not llm_cfg.model:
        raise _err(
            f"config.yaml screen.{stage} is missing `provider` and/or `model`",
            code=1, as_json=json_out,
        )
    spec = ProviderSpec(
        provider=llm_cfg.provider,
        model=llm_cfg.model,
        base_url=llm_cfg.base_url,
        api_key_env=llm_cfg.api_key_env,
    )
    try:
        result = freetext_to_query(description, spec)
    except ImportError as exc:
        raise _err(f"provider SDK not installed: {exc}", as_json=json_out)
    except Exception as exc:
        raise _err(f"query generation failed: {exc}", code=1, as_json=json_out)

    written = False
    if write:
        if not result["valid"]:
            raise _err(
                f"generated query did not parse ({result['parse_error']}); not written",
                code=1, as_json=json_out,
            )
        proto_path = review_dir / "protocol.yaml"
        if not proto_path.exists():
            raise _err(f"no protocol.yaml at {proto_path}", code=2, as_json=json_out)
        import yaml as _yaml
        data = _yaml.safe_load(proto_path.read_text(encoding="utf-8-sig")) or {}
        data["query"] = result["query"]
        with frames.review_write_lock(review_dir):
            frames.atomic_write_text(
                proto_path, _yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
            )
        written = True
        AuditLog(review_dir).append("query-assist", args={"stage": stage, "write": True},
                                    details={"query": result["query"]})

    _emit({
        "query": result["query"],
        "valid": result["valid"],
        "parse_error": result["parse_error"],
        "written_to_protocol": written,
    }, json_out)


@app.command("dedup")
@_exclusive_review_write
def dedup_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Deduplicate source occurrences into final records."""
    proto = load_protocol(review_dir / "protocol.yaml")
    if frames.get_stage(review_dir, "search") is None:
        raise _err("search stage not completed; run `slr search` first", code=2, as_json=json_out)
    rows = frames.active_provenance(review_dir)
    plans = deduplicate_occurrences(rows, fuzzy_threshold=proto.dedup.fuzzy_title_threshold)
    frames.write_deduplicated_records(review_dir, plans_to_payload(plans))
    n_source_occurrences, n_final_records = len(rows), len(plans)
    AuditLog(review_dir).append(
        "dedup",
        args={"threshold": proto.dedup.fuzzy_title_threshold},
        details={"n_source_occurrences": n_source_occurrences,
                 "n_final_records": n_final_records,
                 "n_duplicates_removed": n_source_occurrences - n_final_records},
    )
    frames.set_stage(
        review_dir,
        "dedup",
        sha256_str(
            frames.active_evidence_hash(review_dir, "source_run", "occurrence", "active_snapshot")
            + str(proto.dedup.fuzzy_title_threshold)
        ),
        frames.artifact_hash(review_dir, frames.RECORDS),
    )
    _emit({"n_source_occurrences": n_source_occurrences,
           "n_final_records": n_final_records,
           "n_duplicates_removed": n_source_occurrences - n_final_records,
           "next": "slr screen <dir> --stage title_abstract"}, json_out)


@llm_app.command("test")
def llm_test_cmd(
    review_dir: Annotated[Path, typer.Argument(help="review directory with config.yaml")],
    stage: Annotated[str, typer.Option(help="which configured stage to probe: title_abstract or full_text")] = "title_abstract",
    timeout_s: Annotated[float, typer.Option(help="abort if no response within this many seconds")] = 60.0,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Send a tiny probe to the configured LLM provider for a screening stage.

    Useful for verifying the endpoint is reachable, the model name is correct,
    and JSON output works — before kicking off a long screening run. Does NOT
    write anything to the review's CSV files.
    """
    if stage not in ("title_abstract", "full_text"):
        raise _err(f"invalid stage: {stage}", as_json=json_out)
    proto_path = review_dir / "protocol.yaml"
    if not proto_path.exists():
        raise _err(f"no protocol.yaml at {proto_path}", code=2, as_json=json_out)
    proto = load_protocol(proto_path)
    cfg = load_config_for_review(review_dir)
    llm_cfg = cfg.screen.title_abstract if stage == "title_abstract" else cfg.screen.full_text

    if not llm_cfg.provider or not llm_cfg.model:
        raise _err(
            f"config.yaml screen.{stage} is missing `provider` and/or `model`. "
            f"Set them and re-run.",
            code=1, as_json=json_out,
        )

    # Build a fake final record matching the screener's expected shape.
    fake_record = {
        "record_id": -1,
        "title": "CRISPR-Cas9 off-target effects: a high-throughput assay in T cells",
        "year": 2024,
        "venue": "Nature Biotechnology",
        "abstract": (
            "We developed a high-throughput GUIDE-seq protocol to map off-target "
            "cleavage by Cas9 in primary human T cells. Off-target activity was "
            "quantified at 12 loci across 5 donors."
        ),
    }

    try:
        screener = build_screener(
            ScreenStage(type="llm"),  # methodology stub
            llm_cfg, proto, fulltext_required=False,
        )
    except RuntimeError as exc:
        raise _err(str(exc), as_json=json_out)
    except ImportError as exc:
        raise _err(f"provider SDK not installed: {exc}", as_json=json_out)

    if not json_out:
        sys.stderr.write(
            f"[llm test] provider={llm_cfg.provider} model={llm_cfg.model} "
            f"base_url={llm_cfg.base_url or '(default)'}\n"
        )
        sys.stderr.flush()

    import threading
    import time

    result: dict[str, Any] = {}
    err_box: list[Exception] = []

    def _do() -> None:
        try:
            t0 = time.time()
            decision = screener.decide(fake_record)
            result["decision"] = decision
            result["latency_ms"] = int((time.time() - t0) * 1000)
        except Exception as exc:
            err_box.append(exc)

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        raise _err(
            f"provider did not respond within {timeout_s:.0f}s — check that the "
            f"endpoint is running ({llm_cfg.base_url or 'default'}) and the "
            f"model is loaded",
            code=1, as_json=json_out,
        )
    if err_box:
        raise _err(f"LLM call failed: {err_box[0]!r}", code=1, as_json=json_out)

    d = result["decision"]
    # "ok" means we got a clean JSON-parseable decision — the screener's
    # regex-fallback path uses reason_code='PARSE_ERROR' which is the signal
    # that the model returned non-JSON text.
    parsed_ok = (
        d.decision in ("include", "exclude", "unsure")
        and d.reason_code is not None
        and d.reason_code != "PARSE_ERROR"
    )
    payload: dict[str, Any] = {
        "ok": parsed_ok,
        "provider": llm_cfg.provider,
        "model": llm_cfg.model,
        "base_url": llm_cfg.base_url,
        "stage": stage,
        "latency_ms": result["latency_ms"],
        "decision": d.decision,
        "reason_code": d.reason_code,
        "reason_text": d.reason_text,
        "confidence": d.confidence,
        "raw_response_excerpt": (d.raw_response or "")[:400],
    }
    if not parsed_ok:
        payload["hint"] = (
            "The model responded but didn't produce a parseable JSON decision. "
            "Some local models ignore `response_format: json_object`. "
            "The screener will still extract a decision via regex, but expect "
            "more 'unsure' results. Try a different model (Qwen2.5 is reliable) "
            "or upgrade the local server."
        )
    _emit(payload, json_out)


@app.command("screen")
@_exclusive_review_write
def screen_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    stage: Annotated[str, typer.Option(help="title_abstract or full_text")],
    converter: Annotated[str | None, typer.Option(help="full_text: override PDF conversion — marker | pdf-inspector | anydoc | extend | datalab | server | none")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="suppress progress output")] = False,
    limit: Annotated[int | None, typer.Option(help="cap final records screened (testing)")] = None,
    restart: Annotated[bool, typer.Option("--restart", help="discard existing decisions for this stage and re-screen from scratch")] = False,
    retry_errors: Annotated[bool, typer.Option("--retry-errors", help="re-screen only records whose PDF conversion or screening previously errored")] = False,
) -> None:
    """Run the configured screener over final deduplicated records.

    Decisions are appended to the stage CSV ONE AT A TIME — if the process is killed
    or crashes mid-run, all decisions made so far are preserved. Re-invoking
    `slr screen` resumes automatically, skipping records that already have
    a decision for this stage. Pass `--restart` to wipe and start over.
    """
    if stage not in ("title_abstract", "full_text"):
        raise _err(f"invalid stage: {stage}", as_json=json_out)
    available_converters = ("marker", "pdf-inspector", "anydoc", "extend", "datalab", "server", "none")
    if converter is not None and converter not in available_converters:
        raise _err(f"invalid --converter: {converter} ({' | '.join(available_converters)})", as_json=json_out)
    proto = load_protocol(review_dir / "protocol.yaml")
    cfg = load_config_for_review(review_dir)
    frames.init(review_dir)
    if frames.get_stage(review_dir, "dedup") is None:
        raise _err("dedup stage not completed", code=2, as_json=json_out)

    # Render the runner's structured events as the CLI's live stderr progress.
    inner: dict[str, Any] = {}

    def cli_progress(event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "start":
            converter_name = event.get("converter")
            if not quiet:
                if stage != "full_text":
                    scope = "total records"
                elif event.get("fulltext_mode") == "required":
                    scope = f"records that passed title/abstract and have a PDF (converter: {converter_name})"
                elif event.get("fulltext_mode") == "optional":
                    scope = f"records that passed title/abstract (PDF used when available; converter: {converter_name})"
                else:
                    scope = "records that passed title/abstract (abstract-only)"
                msg = f"[{stage}] screening with {event['screener']}: {event['pending']} pending"
                if event["already_decided"]:
                    msg += f", {event['already_decided']} already decided (resuming)"
                sys.stderr.write(msg + " of " + f"{event['total']} {scope}\n")
                sys.stderr.flush()
            inner["fn"] = _make_screen_progress(
                stage, event["total"], quiet, initial_counts=event["initial_counts"]
            )
        elif kind == "note":
            if not quiet:
                sys.stderr.write(f"[{stage}] {event['message']}\n")
                sys.stderr.flush()
        elif kind in ("decided", "error"):
            fn = inner.get("fn")
            if fn:
                fn(event)
        elif kind == "interrupted":
            if not quiet:
                sys.stderr.write(
                    f"\n[{stage}] interrupted — {event['n_decided_this_run']} decisions written "
                    f"this run, {event['idx']}/{event['total']} cumulative. "
                    f"Re-run `slr screen` to resume.\n"
                )
                sys.stderr.flush()
        elif kind == "done":
            _screen_done(stage, event["counts"], event["n_errors"], quiet)

    try:
        payload = run_screening(
            review_dir, proto, cfg, stage,
            converter=converter, restart=restart, retry_errors=retry_errors,
            limit=limit, progress=cli_progress,
        )
    except RuntimeError as exc:
        raise _err(str(exc), as_json=json_out)
    except KeyboardInterrupt:
        raise typer.Exit(code=130)

    # The runner returns richer fields than the CLI historically emitted; keep
    # the CLI's leaner payload shape.
    out = {"stage": payload["stage"], "screener": payload["screener"],
           "n_decided_this_run": payload["n_decided_this_run"],
           "counts": payload["counts"], "n_errors": payload["n_errors"],
           "next": payload["next"]}
    if stage == "full_text":
        out["converter"] = payload["converter"]
        out["n_conversion_errors"] = payload["n_conversion_errors"]
    _emit(out, json_out)


@app.command("marker-server")
def marker_server_cmd(
    host: Annotated[str, typer.Option(help="interface to bind")] = "0.0.0.0",
    port: Annotated[int, typer.Option(help="port to listen on")] = 8000,
    recycle_after: Annotated[int, typer.Option(help="restart the marker child every N PDFs to bound RAM")] = 50,
) -> None:
    """Run a self-hosted marker conversion server (needs `pip install 'slr-agent[marker]'`).

    Set MARKER_SERVER_KEY to require a bearer token. Clients then set
    MARKER_SERVER_URL (and MARKER_SERVER_KEY) and screen with `--converter server`,
    needing no marker/torch install themselves.
    """
    from slr.fulltext.marker_server import serve
    serve(host=host, port=port, recycle_after=recycle_after)


def _api_keys_from_env() -> dict[str, str]:
    return {
        name: os.environ[var]
        for name, var in {
            "core": "CORE_API_KEY",
            "semantic_scholar": "SEMANTIC_SCHOLAR_API_KEY",
        }.items()
        if var in os.environ
    }


@app.command("fetch")
@_exclusive_review_write
def fetch_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    only_passed: Annotated[bool, typer.Option(help="only fetch records that passed title/abstract screening")] = True,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="suppress progress output")] = False,
) -> None:
    """Run OA resolver chain to download PDFs for screened-in records.

    Writes per-record progress to stderr (downloaded / missing tally with the
    name of the resolver that succeeded). On completion, writes a CSV of
    unresolved papers (metadata + DOI link) for manual collection, plus a
    status table summarising the run.
    """
    cfg = load_config_for_review(review_dir)
    if frames.get_stage(review_dir, "dedup") is None:
        raise _err("dedup stage not completed", code=2, as_json=json_out)
    records = frames.final_records(review_dir)
    # Need raw JSON from the source occurrence selected for each final record.
    raw_map = frames.raw_json_by_final_record_id(review_dir)
    for record in records:
        record["raw_json"] = raw_map.get(record["source_occurrence_id"])

    scope = "all deduplicated records"
    if only_passed:
        passed_ids = frames.included_record_ids(review_dir, "title_abstract")
        if frames.get_stage(review_dir, "screen_title_abstract") is not None:
            records = [record for record in records if record["record_id"] in passed_ids]
            scope = "records included at title/abstract screening"
        else:
            scope = "all deduplicated records (title/abstract screening has not run)"

    fetch_input_hash = sha256_str(jsonlib.dumps({
        "records": frames.artifact_hash(review_dir, frames.RECORDS),
        "title_abstract": frames.artifact_hash(
            review_dir, frames.screen_file("title_abstract")
        ),
        "only_passed": only_passed,
        "fetch_config": cfg.fetch.model_dump(),
    }, sort_keys=True, default=str))
    invalidated = frames.invalidate_if_input_changed(review_dir, "fetch", fetch_input_hash)
    if invalidated and not quiet:
        sys.stderr.write(
            "[fetch] inputs changed; invalidated stale "
            + ", ".join(invalidated) + " output(s)\n"
        )
        sys.stderr.flush()

    n_total = len(records)
    if not quiet:
        sys.stderr.write(
            f"[fetch] attempting OA acquisition for {n_total} records ({scope}) via "
            f"{len(cfg.fetch.sources)} resolver(s): {', '.join(cfg.fetch.sources)}\n"
        )
        sys.stderr.flush()

    acquirer = Acquirer(
        review_dir=review_dir,
        config=cfg.fetch,
        contact_email=cfg.search.contact_email,
        api_keys=_api_keys_from_env(),
    )
    progress = _make_fetch_progress(total=n_total, quiet=quiet)
    attempts = acquirer.acquire_all(records, progress=progress)

    n_downloaded = sum(1 for a in attempts if a.pdf_sha256)
    n_missing = n_total - n_downloaded
    n_resolver_calls = len(attempts)

    _fetch_done(n_downloaded, n_missing, n_resolver_calls, quiet)

    # Write the manual-collection CSV for anything we couldn't get automatically.
    unresolved_csv = review_dir / "unresolved.csv"
    n_unresolved = write_unresolved_csv(review_dir, unresolved_csv)

    # Full text is converted/screened in one step now — no separate convert.
    done_step = f"slr screen {review_dir} --stage full_text"

    # Status table + next-step instructions on stdout (so it's pipeable).
    if not json_out:
        table = _format_status_table([
            ("total papers attempted", n_total),
            ("successfully downloaded", n_downloaded),
            ("still missing (manual)", n_missing),
            ("resolver invocations", n_resolver_calls),
        ])
        sys.stdout.write("\n[fetch] summary\n" + table + "\n")
        if n_unresolved:
            sys.stdout.write(
                f"\n{n_unresolved} paper(s) need manual collection.\n"
                f"  → CSV: {unresolved_csv}\n"
                f"\n  Workflow:\n"
                f"    1. Open the CSV. Each row has a `save_as` column with the exact\n"
                f"       readable filename to use for that paper.\n"
                f"    2. Download the PDF from the `doi_url` (preferred) or `url` column.\n"
                f"    3. Save it into: {review_dir / cfg.fetch.cache_dir}\n"
                f"       — using the filename from `save_as` (do NOT rename).\n"
                f"    4. (Optional) Run `slr request-reprints` to email any authors\n"
                f"       whose papers you can't access directly.\n"
                f"\n  When done, run: {done_step}\n"
                "  (`slr screen --stage full_text` auto-detects the files you placed.)\n"
            )
        else:
            sys.stdout.write(f"\nAll papers downloaded. Run: {done_step}\n")
        sys.stdout.flush()

    AuditLog(review_dir).append(
        "fetch",
        args={"only_passed": only_passed, "n_total": n_total},
        details={
            "n_total": n_total,
            "n_downloaded": n_downloaded,
            "n_missing": n_missing,
            "n_resolver_calls": n_resolver_calls,
            "unresolved_csv": str(unresolved_csv) if n_unresolved else None,
        },
    )
    frames.set_stage(
        review_dir,
        "fetch",
        fetch_input_hash,
        sha256_str(
            frames.artifact_hash(review_dir, frames.FETCH_ATTEMPTS)
            + frames.tree_hash(review_dir / cfg.fetch.cache_dir)
        ),
    )
    _emit({
        "n_total": n_total,
        "n_downloaded": n_downloaded,
        "n_missing": n_missing,
        "n_resolver_calls": n_resolver_calls,
        "scope": scope,
        "unresolved_csv": str(unresolved_csv) if n_unresolved else None,
        "next": (
            done_step if n_missing == 0
            else f"collect missing PDFs manually (see unresolved.csv), then `{done_step}`"
        ),
    }, json_out)


@app.command("request-reprints")
@_exclusive_review_write
def reprints_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    requester_name: Annotated[str, typer.Option(help="your name for the email signature")] = "",
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Write reprints.csv for records where every OA resolver failed."""
    proto = load_protocol(review_dir / "protocol.yaml")
    unresolved = frames.unresolved_record_ids(review_dir)
    name = requester_name or (proto.authors[0] if proto.authors else "Reviewer")
    out = write_reprint_csv(review_dir, proto.title, name, unresolved)
    AuditLog(review_dir).append("request-reprints",
                                details={"n_unresolved": len(unresolved), "path": str(out)})
    _emit({"n_unresolved": len(unresolved), "csv_path": str(out)}, json_out)


@app.command("prisma")
@_exclusive_review_write
def prisma_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compute PRISMA counts and write prisma.json / prisma.mmd / prisma.svg."""
    if not frames.state_path(review_dir).exists():
        raise _err(f"review not initialised at {review_dir}; run `slr init` first", code=2, as_json=json_out)
    outputs = write_prisma_outputs(review_dir)
    AuditLog(review_dir).append("prisma", details=outputs)
    _emit(outputs, json_out)


@app.command("export")
@_exclusive_review_write
def export_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    format: Annotated[str, typer.Option(help="ris|jsonl|csv")] = "ris",
    stage: Annotated[str | None, typer.Option(help="all|included|passed_ta|passed_ft|screened_ta")] = "included",
    out: Annotated[Path | None, typer.Option(help="output path; defaults to exports/<stage>.<format>")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export records (default: included, RIS for Zotero)."""
    if format not in EXPORTERS:
        raise _err(f"unknown format: {format}; supported: {list(EXPORTERS)}", as_json=json_out)
    out_path = out or (review_dir / "exports" / f"{stage or 'all'}.{format}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = EXPORTERS[format](review_dir, out_path, stage=stage)
    AuditLog(review_dir).append("export",
                                args={"format": format, "stage": stage, "out": str(out_path)},
                                details={"n_exported": n})
    _emit({"format": format, "stage": stage, "out": str(out_path), "n_exported": n}, json_out)


@app.command("export-included")
@_exclusive_review_write
def export_included_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    out: Annotated[Path | None, typer.Option(help="output path; defaults to exports/included_fulltext.csv")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Write a CSV of records that passed FULL-TEXT screening.

    One row per included study, with the PDF and markdown paths, the screening
    reasoning (decision, reason code/text, screener) for both the full-text and
    title/abstract stages, and provenance (source, IDs, which databases found
    it, the OA resolver, content hash, and which converter produced the text).
    """
    if not frames.state_path(review_dir).exists():
        raise _err(f"review not initialised at {review_dir}; nothing to export", code=2, as_json=json_out)
    out_path = out or (review_dir / "exports" / "included_fulltext.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = export_fulltext_included(review_dir, out_path)
    AuditLog(review_dir).append("export-included",
                                args={"out": str(out_path)}, details={"n_included": n})
    _emit({"out": str(out_path), "n_included": n}, json_out)


@app.command("inspect")
def inspect_cmd(
    review_dir: Annotated[Path, typer.Argument()],
    json_out: Annotated[bool, typer.Option("--json")] = True,
) -> None:
    """Report the review's current state."""
    proto_path = review_dir / "protocol.yaml"
    if not proto_path.exists():
        raise _err(f"no protocol.yaml at {proto_path}", code=2, as_json=json_out)
    proto = load_protocol(proto_path)
    if not frames.state_path(review_dir).exists():
        frames.init(review_dir)
    stages = {
        s: bool(frames.get_stage(review_dir, s))
        for s in ("search", "dedup", "screen_title_abstract", "fetch", "screen_full_text")
    }
    payload = {
        "review_id": proto.review_id,
        "review_dir": str(review_dir),
        "stages_completed": stages,
        "n_records": frames.record_count(review_dir),
        "n_final_records": frames.record_count(review_dir),
        "next_command": _next_command(stages),
    }
    _emit(payload, json_out)


def _next_command(stages: dict[str, bool]) -> str:
    if not stages["search"]:
        return "slr search <dir>"
    if not stages["dedup"]:
        return "slr dedup <dir>"
    if not stages["screen_title_abstract"]:
        return "slr screen <dir> --stage title_abstract"
    if not stages["fetch"]:
        return "slr fetch <dir>"
    # Full text is converted and screened in one step (`--stage full_text`).
    if not stages["screen_full_text"]:
        return "slr screen <dir> --stage full_text"
    return "slr prisma <dir>"


if __name__ == "__main__":
    app()
