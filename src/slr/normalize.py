"""Normalization helpers shared across sources.

Per-source normalization lives in each Source.normalize(); this module
holds string-level helpers used during dedup and search-term matching.
"""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    s = unicodedata.normalize("NFKD", title)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def normalize_lastname(name: str | None) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii")
    return s.lower().strip().rstrip(",").strip()
