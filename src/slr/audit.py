from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slr.version import __version__


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def sha256_file(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class AuditLog:
    """Append-only JSONL audit log inside a review directory."""

    def __init__(self, review_dir: str | Path):
        self.review_dir = Path(review_dir)
        self.path = self.review_dir / "audit.log.jsonl"

    def append(
        self,
        command: str,
        args: dict[str, Any] | None = None,
        input_hash: str | None = None,
        output_hash: str | None = None,
        status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "ts": utc_now_iso(),
            "tool_version": __version__,
            "command": command,
            "args": args or {},
            "input_hash": input_hash,
            "output_hash": output_hash,
            "status": status,
            "details": details or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
