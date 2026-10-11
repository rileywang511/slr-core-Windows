from __future__ import annotations

from pathlib import Path

from slr import frames


def _complete_pipeline(review: Path) -> None:
    frames.init(review)
    for stage in (
        "search", "dedup", "screen_title_abstract", "fetch", "screen_full_text",
    ):
        frames.set_stage(review, stage, f"{stage}-input", f"{stage}-output")


def test_changed_dedup_invalidates_all_derived_stages_and_outputs(tmp_path: Path):
    _complete_pipeline(tmp_path)
    (tmp_path / frames.screen_file("title_abstract")).write_text("old title decisions")
    (tmp_path / frames.FETCH_ATTEMPTS).write_text("old fetch attempts")
    (tmp_path / frames.CONVERSIONS).write_text("old conversions")
    (tmp_path / frames.screen_file("full_text")).write_text("old full text decisions")

    invalidated = frames.set_stage(tmp_path, "dedup", "new-input", "new-output")

    assert invalidated == ["screen_title_abstract", "fetch", "screen_full_text"]
    assert frames.get_stage(tmp_path, "dedup") is not None
    for stage in ("screen_title_abstract", "fetch", "screen_full_text"):
        assert frames.get_stage(tmp_path, stage) is None
    for name in (
        frames.screen_file("title_abstract"), frames.FETCH_ATTEMPTS,
        frames.CONVERSIONS, frames.screen_file("full_text"),
    ):
        assert not (tmp_path / name).exists()


def test_changed_search_removes_stale_deduplicated_records(tmp_path: Path):
    _complete_pipeline(tmp_path)
    (tmp_path / frames.RECORDS).write_text("old deduplicated records")

    invalidated = frames.set_stage(tmp_path, "search", "new-input", "new-output")

    assert invalidated == ["dedup", "screen_title_abstract", "fetch", "screen_full_text"]
    assert not (tmp_path / frames.RECORDS).exists()


def test_changed_screen_inputs_clear_current_stage_and_dependents(tmp_path: Path):
    _complete_pipeline(tmp_path)
    (tmp_path / frames.screen_file("title_abstract")).write_text("old title decisions")
    (tmp_path / frames.FETCH_ATTEMPTS).write_text("old fetch attempts")

    invalidated = frames.invalidate_if_input_changed(
        tmp_path, "screen_title_abstract", "changed-methodology",
    )

    assert invalidated == ["screen_title_abstract", "fetch", "screen_full_text"]
    assert frames.get_stage(tmp_path, "dedup") is not None
    assert frames.get_stage(tmp_path, "screen_title_abstract") is None
    assert not (tmp_path / frames.screen_file("title_abstract")).exists()
    assert not (tmp_path / frames.FETCH_ATTEMPTS).exists()
