"""Out-of-process marker runner with periodic recycling.

marker-pdf / surya leak native memory over a long batch run — resident memory
climbs and never comes back down until the process exits (see marker issues
#205, #561, #583). Reusing the models in-process avoids reloading them, but the
leak still accumulates. So for full-text screening we run marker in a *child*
process and recycle that child every N PDFs: accumulated native allocations are
reclaimed by process exit, capping peak memory.

Protocol — newline-delimited JSON over the child's stdin/stdout:
  parent -> child : {"pdf": "...", "out": "/tmp/x.txt",
                     "output_format": "markdown", "use_llm": false, "force_ocr": false}
  child  -> parent: one line "@@MARKER@@{...}" — {"ok": true, "pages": N}
                    or {"ok": false, "error": "..."}

The converted text is written by the child to the "out" path (keeps large text
and any stray library stdout off the protocol channel).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SENTINEL = "@@MARKER@@"


# --------------------------------------------------------------------------
# Child side
# --------------------------------------------------------------------------

def _worker_main() -> int:
    # Reserve real stdout for the protocol; send any library prints to stderr so
    # they can't corrupt the JSON channel.
    proto = sys.stdout
    sys.stdout = sys.stderr

    from slr.fulltext.convert import _run_marker  # lazy: imports marker on first use

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            text, pages = _run_marker(
                Path(req["pdf"]),
                output_format=req.get("output_format", "markdown"),
                use_llm=req.get("use_llm", False),
                force_ocr=req.get("force_ocr", False),
            )
            Path(req["out"]).write_text(text, encoding="utf-8")
            resp = {"ok": True, "pages": pages}
        except Exception as exc:  # noqa: BLE001 — report any failure to the parent
            resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        proto.write(SENTINEL + json.dumps(resp) + "\n")
        proto.flush()
    return 0


# --------------------------------------------------------------------------
# Parent side
# --------------------------------------------------------------------------

class MarkerSubprocess:
    """Drives a recycled marker child process. One instance per screening run."""

    def __init__(
        self,
        output_format: str = "markdown",
        use_llm: bool = False,
        force_ocr: bool = False,
        recycle_after: int = 50,
        command: list[str] | None = None,
    ):
        self.output_format = output_format
        self.use_llm = use_llm
        self.force_ocr = force_ocr
        self.recycle_after = max(1, recycle_after)
        self._command = command or [sys.executable, "-m", "slr.fulltext.marker_worker"]
        self._proc: subprocess.Popen | None = None
        self._done_since_spawn = 0

    def convert(self, pdf_path: Path) -> tuple[str, int | None]:
        """Convert one PDF via the child. Raises RuntimeError on failure."""
        self._ensure_alive()
        with tempfile.NamedTemporaryFile(prefix="slr-marker-", suffix=".txt", delete=False) as tmp:
            out_path = tmp.name
        req = json.dumps({
            "pdf": str(pdf_path), "out": out_path,
            "output_format": self.output_format,
            "use_llm": self.use_llm, "force_ocr": self.force_ocr,
        })
        try:
            resp = self._roundtrip(req)
            if resp is None:
                # Child died mid-conversion (e.g. OOM-killed). Recycle and report.
                self._stop()
                raise RuntimeError("marker worker exited before responding (possibly OOM-killed)")
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error") or "marker conversion failed")
            text = Path(out_path).read_text(encoding="utf-8")
            pages = resp.get("pages")
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

        self._done_since_spawn += 1
        if self._done_since_spawn >= self.recycle_after:
            self._stop()  # next convert() spawns a fresh child, reclaiming memory
        return text, pages

    def close(self) -> None:
        self._stop()

    # ---- internals ----

    def _roundtrip(self, request_line: str) -> dict | None:
        assert self._proc is not None and self._proc.stdin and self._proc.stdout
        try:
            self._proc.stdin.write(request_line + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            # Child vanished between spawn and write — respawn once and retry.
            self._stop()
            self._spawn()
            self._proc.stdin.write(request_line + "\n")  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
        while True:
            line = self._proc.stdout.readline()
            if line == "":  # EOF — child exited
                return None
            if line.startswith(SENTINEL):
                return json.loads(line[len(SENTINEL):])
            # ignore any stray stdout noise from the child

    def _ensure_alive(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()

    def _spawn(self) -> None:
        # Quiet by default; set SLR_MARKER_DEBUG=1 to see marker's own output.
        stderr = None if os.environ.get("SLR_MARKER_DEBUG") else subprocess.DEVNULL
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            text=True, encoding="utf-8", bufsize=1, env=env,
        )
        self._done_since_spawn = 0

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        finally:
            if proc.stdout:
                proc.stdout.close()


if __name__ == "__main__":
    raise SystemExit(_worker_main())
