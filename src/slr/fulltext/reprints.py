from __future__ import annotations

import csv
import json
from pathlib import Path

from slr import frames


REPRINT_TEMPLATE = """Dear Dr. {family_name},

I am conducting a systematic literature review on "{review_title}".
I was unable to obtain an open-access copy of your paper:

    "{title}" ({year})
    DOI: {doi}

Would you be willing to share a copy of the full text for my review?

Thank you for considering this request.

Kind regards,
{requester_name}
"""


def find_corresponding_email(authors_json: str | None) -> str | None:
    """Best-effort corresponding-author email from authors_json.

    Most OpenAlex/EuropePMC records don't expose emails; this returns None
    in that case. The CSV still includes a row so the user can look up the
    email themselves (e.g. from the published paper).
    """
    if not authors_json:
        return None
    try:
        authors = json.loads(authors_json)
    except json.JSONDecodeError:
        return None
    for a in authors:
        for k in ("email", "correspondingEmail", "corresponding_email"):
            v = a.get(k)
            if isinstance(v, str) and "@" in v:
                return v
    return None


def write_reprint_csv(
    review_dir: Path,
    review_title: str,
    requester_name: str,
    unresolved_record_ids: list[int],
) -> Path:
    out = review_dir / "reprints.csv"
    if not unresolved_record_ids:
        out.write_text("record_id,doi,title,year,first_author,email,suggested_body\n", encoding="utf-8")
        return out

    rows = list(frames.records_by_id(review_dir, unresolved_record_ids).values())

    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "doi", "title", "year", "first_author", "email", "suggested_body"])
        for row in rows:
            family = _first_author_family(row.get("authors_json"))
            email = find_corresponding_email(row.get("authors_json")) or ""
            body = REPRINT_TEMPLATE.format(
                family_name=family or "Author",
                review_title=review_title,
                title=row.get("title") or "",
                year=row.get("year") or "",
                doi=row.get("doi") or "(no DOI)",
                requester_name=requester_name,
            )
            w.writerow([
                row["record_id"],
                row.get("doi") or "",
                row.get("title") or "",
                row.get("year") or "",
                family or "",
                email,
                body,
            ])
    return out


def _first_author_family(authors_json: str | None) -> str | None:
    if not authors_json:
        return None
    try:
        authors = json.loads(authors_json)
    except json.JSONDecodeError:
        return None
    for a in authors:
        if a.get("family"):
            return a["family"]
    return None
