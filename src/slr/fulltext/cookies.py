"""Minimal, explicit support for browser-exported Netscape cookie jars."""

from __future__ import annotations

from pathlib import Path

import httpx


def load_netscape_cookie_jar(path_value: str) -> httpx.Cookies:
    """Load a Netscape/Mozilla cookies.txt file without persisting its values.

    This format is what common browser extensions export. Cookies stay in
    memory and are applied only to the institutional-proxy download request.
    """
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise ValueError(f"proxy.cookie_jar does not exist or is not a file: {path}")

    cookies = httpx.Cookies()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), start=1):
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        if line.startswith("#HttpOnly_"):
            line = line.removeprefix("#HttpOnly_")
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError(f"invalid Netscape cookie jar at {path}:{line_number}")
        domain, _include_subdomains, cookie_path, secure, _expiry, name, value = fields
        if not domain or not name:
            raise ValueError(f"invalid Netscape cookie jar at {path}:{line_number}")
        cookies.set(name, value, domain=domain, path=cookie_path or "/")
    return cookies
