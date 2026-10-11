from __future__ import annotations

import re
from typing import Any

from slr.screen.base import Decision, Screener


class RulesScreener(Screener):
    """Rule-based screener.

    Each rule is a dict:
      {"action": "exclude"|"include", "field": "title"|"abstract"|"any",
       "pattern": "<regex>", "reason_code": "E1", "reason_text": "..."}

    Rules evaluated in order; first match wins. Unmatched -> 'unsure'.
    """

    name = "rules-v1"

    def __init__(self, rules: list[dict[str, Any]] | None = None):
        self.rules = [
            {
                **r,
                "_regex": re.compile(r["pattern"], re.IGNORECASE),
            }
            for r in (rules or [])
            if r.get("pattern")
        ]

    def decide(
        self,
        record: dict[str, Any],
        fulltext: str | None = None,
        fulltext_pdf: Any = None,  # rules operate on text only; PDFs are ignored
    ) -> Decision:
        haystacks = {
            "title": record.get("title") or "",
            "abstract": record.get("abstract") or "",
        }
        if fulltext:
            haystacks["fulltext"] = fulltext
        haystacks["any"] = "\n".join(haystacks.values())

        for rule in self.rules:
            text = haystacks.get(rule.get("field", "any"), haystacks["any"])
            if rule["_regex"].search(text):
                return Decision(
                    record_id=record["record_id"],
                    decision=rule["action"],
                    reason_code=rule.get("reason_code"),
                    reason_text=rule.get("reason_text") or rule["pattern"],
                )
        return Decision(
            record_id=record["record_id"],
            decision="unsure",
            reason_code="NO_RULE_MATCHED",
            reason_text="no rule matched",
        )
