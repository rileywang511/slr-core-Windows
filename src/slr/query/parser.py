from __future__ import annotations

import re
from dataclasses import dataclass

from slr.query.ast import (
    ALL_FIELDS,
    And,
    Expr,
    Field,
    FieldTag,
    Near,
    Not,
    Or,
    Term,
    YearRange,
)


class ParseError(ValueError):
    pass


# Token kinds
TOK_FIELD = "FIELD"        # e.g. TS=
TOK_LPAREN = "LPAREN"
TOK_RPAREN = "RPAREN"
TOK_AND = "AND"
TOK_OR = "OR"
TOK_NOT = "NOT"
TOK_NEAR = "NEAR"          # NEAR or NEAR/n
TOK_PHRASE = "PHRASE"
TOK_WORD = "WORD"
TOK_EOF = "EOF"


@dataclass
class Token:
    kind: str
    value: str | None = None
    distance: int | None = None  # for NEAR/n


_FIELD_RE = re.compile(r"^(" + "|".join(ALL_FIELDS) + r")\s*=", re.IGNORECASE)
_NEAR_RE = re.compile(r"^NEAR(?:/(\d+))?\b", re.IGNORECASE)
_KEYWORDS = {"AND", "OR", "NOT"}


def tokenize(source: str) -> list[Token]:
    s = source.strip()
    i = 0
    tokens: list[Token] = []
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(Token(TOK_LPAREN))
            i += 1
            continue
        if c == ")":
            tokens.append(Token(TOK_RPAREN))
            i += 1
            continue
        if c == '"':
            j = s.find('"', i + 1)
            if j < 0:
                raise ParseError(f"unterminated phrase at offset {i}")
            tokens.append(Token(TOK_PHRASE, s[i + 1 : j]))
            i = j + 1
            continue

        # field tag (case-insensitive)
        m = _FIELD_RE.match(s[i:])
        if m:
            tokens.append(Token(TOK_FIELD, m.group(1).upper()))
            i += m.end()
            continue

        # NEAR or NEAR/n
        m = _NEAR_RE.match(s[i:])
        if m:
            dist = int(m.group(1)) if m.group(1) else 5
            tokens.append(Token(TOK_NEAR, "NEAR", distance=dist))
            i += m.end()
            continue

        # bareword (operator keyword or term/year)
        j = i
        while j < n and not s[j].isspace() and s[j] not in '()"':
            j += 1
        word = s[i:j]
        i = j
        upper = word.upper()
        if upper in _KEYWORDS:
            tokens.append(Token({"AND": TOK_AND, "OR": TOK_OR, "NOT": TOK_NOT}[upper]))
        else:
            tokens.append(Token(TOK_WORD, word))

    tokens.append(Token(TOK_EOF))
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[self.pos + offset]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, kind: str) -> Token:
        t = self.advance()
        if t.kind != kind:
            raise ParseError(f"expected {kind}, got {t.kind} ({t.value!r}) at pos {self.pos - 1}")
        return t

    # Grammar:
    #   query     := or_expr
    #   or_expr   := and_expr ('OR' and_expr)*
    #   and_expr  := unary_expr (('AND' | 'NOT' | NEAR/n) unary_expr)*
    #   unary_expr:= 'NOT' primary | primary
    #   primary   := field_expr | '(' or_expr ')' | term_or_year
    #   field_expr:= FIELD ('(' or_expr ')' | term_or_year)
    #   term_or_year := YEAR_RANGE | PHRASE | WORD

    def parse_query(self) -> Expr:
        expr = self.parse_or()
        if self.peek().kind != TOK_EOF:
            t = self.peek()
            raise ParseError(f"trailing tokens at pos {self.pos}: {t.kind} {t.value!r}")
        return expr

    def parse_or(self) -> Expr:
        left = self.parse_and()
        while self.peek().kind == TOK_OR:
            self.advance()
            right = self.parse_and()
            left = Or(left, right)
        return left

    def parse_and(self) -> Expr:
        left = self.parse_unary()
        while self.peek().kind in (TOK_AND, TOK_NOT, TOK_NEAR):
            op = self.advance()
            right = self.parse_unary()
            if op.kind == TOK_AND:
                left = And(left, right)
            elif op.kind == TOK_NOT:
                left = And(left, Not(right))
            else:  # NEAR
                left = Near(left, right, distance=op.distance or 5)
        return left

    def parse_unary(self) -> Expr:
        if self.peek().kind == TOK_NOT:
            self.advance()
            return Not(self.parse_primary())
        return self.parse_primary()

    def parse_primary(self) -> Expr:
        t = self.peek()
        if t.kind == TOK_FIELD:
            self.advance()
            field_tag = t.value  # type: ignore[assignment]
            assert field_tag is not None
            inner = self._parse_field_operand(field_tag)
            return Field(field_tag, inner)  # type: ignore[arg-type]
        if t.kind == TOK_LPAREN:
            self.advance()
            expr = self.parse_or()
            self.expect(TOK_RPAREN)
            return expr
        return self._parse_term_or_year(None)

    def _parse_field_operand(self, field_tag: FieldTag) -> Expr:
        t = self.peek()
        if t.kind == TOK_LPAREN:
            self.advance()
            expr = self.parse_or()
            self.expect(TOK_RPAREN)
            return expr
        return self._parse_term_or_year(field_tag)

    def _parse_term_or_year(self, field_tag: FieldTag | None) -> Expr:
        t = self.advance()
        if t.kind == TOK_PHRASE:
            return Term(value=t.value or "", field=field_tag, is_phrase=True)
        if t.kind == TOK_WORD:
            v = t.value or ""
            # detect a year range "2015-2026" (only meaningful for PY)
            if (field_tag == "PY" or _is_year_range(v)) and _is_year_range(v):
                a, b = v.split("-", 1)
                return YearRange(start=int(a), end=int(b))
            # bare year for PY=2020
            if field_tag == "PY" and v.isdigit() and len(v) == 4:
                yr = int(v)
                return YearRange(start=yr, end=yr)
            return Term(value=v, field=field_tag, is_phrase=False)
        raise ParseError(f"unexpected token {t.kind} ({t.value!r})")


def _is_year_range(s: str) -> bool:
    if "-" not in s:
        return False
    parts = s.split("-")
    if len(parts) != 2:
        return False
    return all(p.isdigit() and len(p) == 4 for p in parts)


def parse(source: str) -> Expr:
    """Parse a WoS-style query string into an AST."""
    return _Parser(tokenize(source)).parse_query()
