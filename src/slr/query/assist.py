"""Turn a plain-English research description into a WoS-style boolean query.

This is a first-class `slr` CLI feature (`slr query-assist`). It asks an LLM to
emit a query in the exact grammar that :func:`slr.query.parse` understands,
then validates the
result by parsing it — so a query that comes back here is guaranteed to be
runnable by every source emitter (or the caller is told why it isn't).
"""

from __future__ import annotations

import re
from typing import Any

from slr.llm.base import Message, Provider, ProviderSpec
from slr.llm.factory import make_provider
from slr.query import ParseError, parse

# The grammar lives in one place so the model prompt and the docs can't drift
# from what the parser (slr/query/parser.py) actually accepts.
QUERY_GRAMMAR = """\
Field tags (prefix a term or a parenthesised group with `TAG=`):
  TS  topic — matches title, abstract and keywords (use this by default)
  TI  title only
  AB  abstract only
  AU  author surname
  SO  source / journal name
  DT  document type (e.g. Article, Review)
  LA  language (e.g. English)
  PY  publication year — a single year (PY=2024) or a range (PY=2015-2026)

Operators (UPPERCASE), highest precedence first:
  ( )            grouping
  NOT x          negation
  x NEAR/n y     x and y within n words (n optional, defaults to 5)
  x AND y        both
  x OR y         either

Terms:
  "multi word"   a quoted phrase is matched exactly
  wildcard*      `*` matches any suffix, `?` matches one character
A field tag distributes over a parenthesised group, e.g.
  TS=("antibiotic resistance" OR "AMR")
"""

SYSTEM_PROMPT = f"""You convert a researcher's plain-English description of what \
they want to find into a single Web-of-Science–style boolean search query.

Use ONLY this grammar — anything else will be rejected:

{QUERY_GRAMMAR}

Rules:
- Group synonyms/related terms with OR inside parentheses, and join the distinct
  concepts with AND. Quote multi-word phrases.
- Prefer the TS (topic) tag for concept terms. Add a PY=… year constraint only if
  the user gave a date range or years.
- Use wildcards (e.g. contaminat*) to capture word variants where helpful.
- Return ONLY the query text. No explanation, no code fences, no leading label.
"""


def _strip_to_query(text: str) -> str:
    """Pull the bare query out of a model response (tolerate fences / labels)."""
    t = text.strip()
    # Strip ``` fenced blocks if present.
    fence = re.search(r"```(?:\w+)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # Drop a leading "Query:" style label on the first line.
    t = re.sub(r"^\s*(query|search(?:\s*string)?)\s*[:=]\s*", "", t, flags=re.IGNORECASE)
    return t.strip()


def freetext_to_query(
    description: str,
    provider_spec: ProviderSpec,
    *,
    model: str | None = None,
    provider: Provider | None = None,
) -> dict[str, Any]:
    """Generate a WoS-style query from free text and validate it.

    Args:
        description: the researcher's plain-English description.
        provider_spec: how to reach the LLM (provider/model/base_url/api_key_env).
        model: override the model name (defaults to ``provider_spec.model``).
        provider: inject a ready Provider (used by tests); otherwise built from spec.

    Returns a dict: ``{query, valid, parse_error, raw_response}``. ``valid`` is
    True iff the query parses cleanly; when False, ``parse_error`` explains why
    so the caller can show it and let the user edit before saving.
    """
    if not description or not description.strip():
        raise ValueError("description is empty")

    prov = provider or make_provider(provider_spec)
    model_name = model or provider_spec.model

    resp = prov.complete(
        system=SYSTEM_PROMPT,
        messages=[Message(role="user", content=description.strip())],
        model=model_name,
        max_tokens=512,
        temperature=0.0,
        response_format="text",
    )
    query = _strip_to_query(resp.text)

    parse_error: str | None = None
    valid = True
    try:
        parse(query)
    except ParseError as exc:
        valid = False
        parse_error = str(exc)

    return {
        "query": query,
        "valid": valid,
        "parse_error": parse_error,
        "raw_response": resp.text,
    }
