"""MarkerSubprocess drives a recycled child process. Tested with a fake worker
script so no real marker install is needed."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from slr.fulltext.marker_worker import MarkerSubprocess

# A stand-in worker speaking the same @@MARKER@@ protocol. It records its PID so
# the test can prove the process is recycled, and can be told to crash.
_FAKE_WORKER = textwrap.dedent(
    """
    import json, os, sys
    proto = sys.stdout
    sys.stdout = sys.stderr
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        req = json.loads(raw)
        if "crash" in req["pdf"]:
            os._exit(1)  # die mid-conversion, before responding
        with open(req["out"], "w", encoding="utf-8") as fh:
            fh.write(f"text for {os.path.basename(req['pdf'])} pid={os.getpid()}")
        proto.write("@@MARKER@@" + json.dumps({"ok": True, "pages": os.getpid()}) + "\\n")
        proto.flush()
    """
)


def _pool(tmp_path: Path, recycle_after: int) -> MarkerSubprocess:
    worker = tmp_path / "fake_worker.py"
    worker.write_text(_FAKE_WORKER)
    return MarkerSubprocess(
        recycle_after=recycle_after,
        command=[sys.executable, str(worker)],
    )


def test_converts_and_returns_text(tmp_path):
    pool = _pool(tmp_path, recycle_after=100)
    try:
        text, pid = pool.convert(Path("paper1.pdf"))
        assert "text for paper1.pdf" in text
        assert isinstance(pid, int)
    finally:
        pool.close()


def test_recycles_child_after_n_conversions(tmp_path):
    pool = _pool(tmp_path, recycle_after=2)
    try:
        # pages == the worker PID; recycling after 2 means a new PID appears.
        _, pid1 = pool.convert(Path("a.pdf"))
        _, pid2 = pool.convert(Path("b.pdf"))   # 2nd conversion triggers recycle
        _, pid3 = pool.convert(Path("c.pdf"))   # served by a fresh child
        assert pid1 == pid2
        assert pid3 != pid1
    finally:
        pool.close()


def test_worker_death_raises(tmp_path):
    pool = _pool(tmp_path, recycle_after=100)
    try:
        with pytest.raises(RuntimeError, match="exited before responding"):
            pool.convert(Path("please-crash.pdf"))
        # Pool recovers for the next PDF.
        text, _ = pool.convert(Path("ok.pdf"))
        assert "text for ok.pdf" in text
    finally:
        pool.close()


def test_unicode_pdf_name_and_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    pool = _pool(tmp_path, recycle_after=100)
    try:
        text, _ = pool.convert(tmp_path / "中文 paper 🧪.pdf")
        assert "中文 paper 🧪.pdf" in text
    finally:
        pool.close()
