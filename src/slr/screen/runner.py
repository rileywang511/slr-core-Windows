"""Reusable screening orchestration.

This is the engine behind `slr screen`: it walks the final deduplicated records for
a stage, resolves/converts full-text when needed, runs the configured screener,
and appends every decision to its CSV one at a time (so a crash loses at most
the in-flight item). Both the CLI (`screen_cmd`) and other callers use
:func:`run_screening`; the only thing they customise is the `progress` callback.

Progress is reported as structured event dicts so any front-end can render it:

  {"kind": "start",   "stage", "total", "pending", "already_decided",
                      "initial_counts", "converter", "screener"}
  {"kind": "note",    "message"}                      # informational
  {"kind": "decided", "idx", "record_id", "decision"}
  {"kind": "error",   "idx", "record_id", "error"}
  {"kind": "interrupted", "idx", "total", "n_decided_this_run"}
  {"kind": "done",    "counts", "n_errors", "n_conversion_errors", "n_decided_this_run"}
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path
from typing import Any, Callable

from slr.audit import AuditLog, sha256_str
from slr.config import Config, LLMStageConfig
from slr.fulltext.convert import FulltextConverter
from slr.fulltext.naming import target_basename_for_row
from slr.fulltext.scan_manual import scan_for_manual_pdfs
from slr.llm.base import ProviderSpec
from slr.llm.factory import make_provider
from slr.protocol import Protocol, ScreenStage
from slr.screen.base import Decision
from slr.screen.llm import LLMScreener
from slr.screen.rules import RulesScreener
from slr import frames

ProgressFn = Callable[[dict[str, Any]], None]


def resolve_pdf_for_record(
    review_dir: Path, cache_dir: str, record: dict[str, Any], stored_rel_path: str | None
) -> Path | None:
    """Locate the PDF to screen, preferring the canonical readable filename.

    The stored `pdf_path` can be stale — e.g. after a file was renamed. So we
    resolve in order:
      1. the canonical `pdfs/<readable-name>--r<record-id>.pdf`, if present;
      2. the path recorded in acquisition evidence, if that file still exists.
    Returns None if neither is on disk.
    """
    canonical = review_dir / cache_dir / target_basename_for_row(record)
    if canonical.exists():
        return canonical
    if stored_rel_path:
        stored = review_dir / stored_rel_path
        if stored.exists():
            return stored
    return None


def build_screener(
    stage_cfg: ScreenStage,
    llm_cfg: LLMStageConfig,
    proto: Protocol,
    fulltext_required: bool,
):
    """Build a Screener from (protocol methodology, config plumbing).

    `stage_cfg` describes WHAT (type=llm/rules, criteria, rules patterns).
    `llm_cfg` describes HOW (provider, model, base_url, api_key_env).
    """
    if stage_cfg.type == "rules":
        return RulesScreener(rules=stage_cfg.rules)
    if stage_cfg.type == "llm":
        if not llm_cfg.provider or not llm_cfg.model:
            raise RuntimeError(
                "LLM screener requires `provider` and `model` in config.yaml under "
                "screen.<stage>"
            )
        spec = ProviderSpec(
            provider=llm_cfg.provider,
            model=llm_cfg.model,
            base_url=llm_cfg.base_url,
            api_key_env=llm_cfg.api_key_env,
        )
        provider = make_provider(spec)
        return LLMScreener(
            provider=provider,
            model=llm_cfg.model,
            review_title=proto.title,
            inclusion=proto.inclusion_criteria,
            exclusion=proto.exclusion_criteria,
            require_fulltext=fulltext_required,
        )
    raise RuntimeError(
        f"stage type {stage_cfg.type} not supported for automated screening "
        f"(use manual workflow)"
    )


def run_screening(
    review_dir: Path,
    proto: Protocol,
    cfg: Config,
    stage: str,
    *,
    converter: str | None = None,
    restart: bool = False,
    retry_errors: bool = False,
    limit: int | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Screen the final deduplicated records for ``stage``, writing decisions as it goes.

    Returns a result payload (stage, screener, counts, n_errors, ...). Raises
    RuntimeError if the screener can't be built (e.g. manual stage, missing LLM
    config). KeyboardInterrupt propagates after decisions/audit are persisted.
    """
    frames.init(review_dir)

    stage_cfg = proto.screen.title_abstract if stage == "title_abstract" else proto.screen.full_text
    llm_cfg = cfg.screen.title_abstract if stage == "title_abstract" else cfg.screen.full_text
    fulltext_required = stage == "full_text" and stage_cfg.fulltext == "required"
    screener = build_screener(stage_cfg, llm_cfg, proto, fulltext_required=fulltext_required)

    # How each PDF becomes model input (full_text only): local or hosted converter | none.
    converter_name = (
        "none" if stage == "full_text" and stage_cfg.fulltext == "ignored"
        else (converter or llm_cfg.converter) if stage == "full_text" else "none"
    )

    def emit(event: dict[str, Any]) -> None:
        if progress:
            progress(event)

    records = frames.final_records(review_dir)
    resolved_pdf: dict[int, Path] = {}      # record_id -> on-disk PDF
    fconv: FulltextConverter | None = None
    if stage == "full_text":
        # Full-text review always follows title/abstract inclusion when that
        # stage has run; the fulltext mode determines only the evidence used.
        passed_ids = frames.included_record_ids(review_dir, "title_abstract")
        if frames.get_stage(review_dir, "screen_title_abstract") is not None:
            records = [record for record in records if record["record_id"] in passed_ids]
        else:
            emit({"kind": "note", "message": (
                "title/abstract screening has not run — "
                "screening all records")})

    if stage == "full_text" and stage_cfg.fulltext != "ignored":
        # Pick up any manually-placed PDFs so they're screened too.
        scan_for_manual_pdfs(review_dir, cfg.fetch.cache_dir)
        pdf_attempts = frames.successful_pdf_per_record(review_dir)

        for record in records:
            pdf_path = resolve_pdf_for_record(
                review_dir, cfg.fetch.cache_dir, record, pdf_attempts.get(record["record_id"])
            )
            if pdf_path is not None:
                resolved_pdf[record["record_id"]] = pdf_path

        # Required means a PDF is a prerequisite. Optional uses a PDF when it
        # exists, otherwise screens title/abstract evidence. Ignored means
        # deliberately abstract-only and skips this whole acquisition branch.
        if stage_cfg.fulltext == "required":
            records = [record for record in records if record["record_id"] in resolved_pdf]

        if converter_name in ("marker", "extend", "datalab", "server", "pdf-inspector", "anydoc"):
            try:
                fconv = FulltextConverter(review_dir, converter_name, cfg.fetch)
            except (RuntimeError, ValueError) as exc:
                raise RuntimeError(str(exc)) from exc

    if limit:
        records = records[:limit]
    total = len(records)

    stage_name = f"screen_{stage}"
    stage_inputs: dict[str, Any] = {
        "records": frames.artifact_hash(review_dir, frames.RECORDS),
        "screening": stage_cfg.model_dump(),
        # These fields form the LLM system prompt even though they sit outside
        # the per-stage protocol block.
        "prompt_methodology": {
            "review_title": proto.title,
            "inclusion_criteria": [criterion.model_dump() for criterion in proto.inclusion_criteria],
            "exclusion_criteria": [criterion.model_dump() for criterion in proto.exclusion_criteria],
        },
        "llm": llm_cfg.model_dump(),
        "screener": screener.screener_id(),
        "converter": converter_name if stage == "full_text" else None,
        "fulltext_mode": stage_cfg.fulltext if stage == "full_text" else None,
    }
    if stage == "full_text" and stage_cfg.fulltext != "ignored":
        stage_inputs.update({
            "title_abstract": frames.artifact_hash(
                review_dir, frames.screen_file("title_abstract")
            ),
            "fetch": frames.artifact_hash(review_dir, frames.FETCH_ATTEMPTS),
            "pdfs": frames.tree_hash(review_dir / cfg.fetch.cache_dir),
        })
    stage_input_hash = sha256_str(jsonlib.dumps(stage_inputs, sort_keys=True, default=str))
    invalidated = frames.invalidate_if_input_changed(review_dir, stage_name, stage_input_hash)
    if invalidated:
        emit({"kind": "note", "message": (
            "inputs changed; invalidated stale " + ", ".join(invalidated) + " output(s)"
        )})

    # --- Resume support ---
    if restart:
        n_cleared = frames.clear_stage_decisions(review_dir, stage)
        emit({"kind": "note", "message": f"--restart: cleared {n_cleared} existing decision(s)"})
    elif retry_errors:
        retry_ids = frames.record_ids_with_screener_error(review_dir, stage)
        if stage == "full_text":
            retry_ids |= frames.record_ids_with_conversion_error(review_dir)
            frames.clear_errored_conversions(review_dir)  # so the engine re-runs for them
        n_cleared = frames.clear_stage_decisions_for(review_dir, stage, retry_ids)
        emit({"kind": "note", "message": (
            f"--retry-errors: re-screening {len(retry_ids)} errored record(s) "
            f"({n_cleared} decision(s) cleared)")})

    already_decided = frames.record_ids_with_decisions(review_dir, stage)
    pending = [record for record in records if record["record_id"] not in already_decided]
    initial_counts = frames.decision_counts(review_dir, stage)

    emit({
        "kind": "start", "stage": stage, "total": total, "pending": len(pending),
        "already_decided": len(already_decided), "initial_counts": dict(initial_counts),
        "converter": converter_name if stage == "full_text" else None,
        "fulltext_mode": stage_cfg.fulltext if stage == "full_text" else None,
        "screener": screener.screener_id(),
    })

    counts = dict(initial_counts)
    errors: list[dict[str, Any]] = []
    conversion_errors = 0
    idx = len(already_decided)  # progress index continues from the resume point
    n_decided_this_run = 0
    try:
        for record in pending:
            idx += 1
            decide_kwargs: dict[str, Any] = {"fulltext": None}
            abs_pdf = resolved_pdf.get(record["record_id"]) if stage == "full_text" else None
            if abs_pdf is not None:
                if converter_name == "none":
                    decide_kwargs["fulltext_pdf"] = abs_pdf
                elif fconv is not None:
                    text, conv_err = fconv.convert_text(record["record_id"], abs_pdf)
                    if conv_err:
                        conversion_errors += 1
                        errors.append({"record_id": record["record_id"],
                                       "error": f"convert ({converter_name}): {conv_err}"})
                    decide_kwargs["fulltext"] = text
            try:
                d = screener.decide(record, **decide_kwargs)
            except Exception as exc:
                errors.append({"record_id": record["record_id"], "error": str(exc)})
                emit({"kind": "error", "record_id": record["record_id"],
                      "idx": idx, "error": str(exc)})
                d = Decision(
                    record_id=record["record_id"],
                    decision="unsure",
                    reason_code="SCREENER_ERROR",
                    reason_text=str(exc)[:300],
                )
            # Write the decision IMMEDIATELY so a crash doesn't lose it.
            frames.append_decision(review_dir, stage, {
                "record_id": d.record_id,
                "decision": d.decision,
                "reason_code": d.reason_code,
                "reason_text": d.reason_text,
                "screener": screener.screener_id(),
                "model_id": d.model_id,
                "prompt": d.prompt,
                "response": d.raw_response,
                "input_tokens": d.input_tokens,
                "output_tokens": d.output_tokens,
                "latency_ms": d.latency_ms,
            })
            counts[d.decision] = counts.get(d.decision, 0) + 1
            n_decided_this_run += 1
            emit({"kind": "decided", "idx": idx,
                  "record_id": record["record_id"], "decision": d.decision})
    except KeyboardInterrupt:
        emit({"kind": "interrupted", "idx": idx, "total": total,
              "n_decided_this_run": n_decided_this_run})
        AuditLog(review_dir).append(
            "screen", args={"stage": stage, "screener": screener.screener_id(), "limit": limit},
            details={"n_decided_this_run": n_decided_this_run,
                     "cumulative_counts": counts, "n_errors": len(errors)},
            status="interrupted",
        )
        raise
    finally:
        if fconv is not None:
            fconv.close()  # tear down the marker child process, if any

    emit({"kind": "done", "counts": counts, "n_errors": len(errors),
          "n_conversion_errors": conversion_errors, "n_decided_this_run": n_decided_this_run})

    AuditLog(review_dir).append(
        "screen",
        args={"stage": stage, "screener": screener.screener_id(),
              "converter": converter_name if stage == "full_text" else None,
              "limit": limit, "restart": restart, "retry_errors": retry_errors},
        details={"n_decided_this_run": n_decided_this_run,
                 "n_already_decided_at_start": len(already_decided),
                 "cumulative_counts": counts, "n_errors": len(errors),
                 "n_conversion_errors": conversion_errors,
                 "errors": errors[:20]},
        status="ok" if not errors else "partial",
    )
    frames.set_stage(
        review_dir,
        stage_name,
        stage_input_hash,
        frames.artifact_hash(review_dir, frames.screen_file(stage)),
    )
    next_cmd = "slr fetch" if stage == "title_abstract" else "slr prisma"
    payload: dict[str, Any] = {
        "stage": stage, "screener": screener.screener_id(),
        "n_decided_this_run": n_decided_this_run,
        "already_decided": len(already_decided), "total": total,
        "counts": counts, "n_errors": len(errors), "errors": errors[:20],
        "next": next_cmd,
    }
    if stage == "full_text":
        payload["converter"] = converter_name
        payload["n_conversion_errors"] = conversion_errors
    return payload
