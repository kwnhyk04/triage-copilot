"""The PromQL subset the sim answers: vector selectors such as name{label="value"}, nothing else.

The label matchers and quoted strings here also parse LogQL's stream selectors (sim.logql).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import re2  # pyright: ignore[reportMissingTypeStubs] - google-re2 ships no type stubs

NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
LABEL = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
MATCH_OP = re.compile(r"=~|!~|!=|=")
STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`')
ESCAPES = {"\\": "\\", '"': '"', "'": "'", "n": "\n", "t": "\t", "r": "\r"}
# RE2 reads a repeat count of 10 or more digits as literal text, where Prometheus rejects it.
HUGE_REPEAT = re.compile(r"\{\d{10,}")
RE2_OPTIONS = re2.Options()
RE2_OPTIONS.log_errors = False
# Callers only test for a match, so RE2 can skip the submatch search that capture groups need.
RE2_OPTIONS.never_capture = True
RE2_ERROR = cast(type[Exception], re2.error)  # pyright: ignore[reportUnknownMemberType]


class QueryError(ValueError):
    """A query outside the supported subset; its message goes back to the caller."""


class Regex(Protocol):
    """A compiled RE2 pattern, which matches in time linear in the text."""

    def search(self, text: str) -> object: ...

    def fullmatch(self, text: str) -> object: ...


def regex(value: str) -> Regex:
    """Compiles a caller's pattern with RE2, never Python's backtracking re."""
    if found := HUGE_REPEAT.search(value):
        reason = f"repetition count {found[0][1:]} is over 1000"
    else:
        try:
            return re2.compile(value, RE2_OPTIONS)  # pyright: ignore[reportUnknownMemberType]
        except RE2_ERROR as error:
            # RE2 reports its reason as bytes.
            detail = error.args[0] if error.args else ""
            reason = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
        except (OverflowError, RecursionError):
            reason = "the pattern is too large or nests too deeply"
    raise QueryError(
        f"invalid regex {value!r}: {reason}; use RE2 syntax, no lookarounds or backreferences"
    )


@dataclass(frozen=True)
class Matcher:
    label: str
    op: str
    value: str
    pattern: Regex | None

    @classmethod
    def make(cls, label: str, op: str, value: str) -> "Matcher":
        return cls(label, op, value, regex(value) if "~" in op else None)

    def matches(self, labels: Mapping[str, str]) -> bool:
        """A missing label counts as empty, as in Prometheus; regexes are fully anchored."""
        actual = labels.get(self.label, "")
        if self.pattern is None:
            return (actual == self.value) == (self.op == "=")
        return (self.pattern.fullmatch(actual) is not None) == (self.op == "=~")


class Scanner:
    """Reads a query left to right, skipping whitespace between tokens."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def done(self) -> bool:
        self._skip()
        return self.pos == len(self.text)

    def accept(self, literal: str) -> bool:
        self._skip()
        if self.text.startswith(literal, self.pos):
            self.pos += len(literal)
            return True
        return False

    def take(self, pattern: re.Pattern[str]) -> str | None:
        self._skip()
        found = pattern.match(self.text, self.pos)
        if found is None:
            return None
        self.pos = found.end()
        return found[0]

    def need(self, pattern: re.Pattern[str], what: str) -> str:
        token = self.take(pattern)
        if token is None:
            raise QueryError(f"expected {what} at position {self.pos}")
        return token

    def string(self) -> str:
        """A quoted string: double or single quotes with escapes, or a raw backtick string."""
        raw = self.need(STRING, "a quoted string")
        if raw[0] == "`":
            return raw[1:-1]
        out: list[str] = []
        chars = iter(raw[1:-1])
        for char in chars:
            if char == "\\":
                escaped = next(chars)
                if escaped not in ESCAPES:
                    raise QueryError(f"unknown escape sequence \\{escaped}")
                char = ESCAPES[escaped]
            out.append(char)
        return "".join(out)

    def matchers(self) -> list[Matcher]:
        """A brace-enclosed list such as {service="checkout", level=~"warn|error"}."""
        if not self.accept("{"):
            raise QueryError(f"expected {{ at position {self.pos}")
        found: list[Matcher] = []
        while not self.accept("}"):
            label = self.need(LABEL, "a label name")
            op = self.need(MATCH_OP, "=, !=, =~ or !~")
            found.append(Matcher.make(label, op, self.string()))
            if not self.accept(","):
                if not self.accept("}"):
                    raise QueryError(f"expected , or }} at position {self.pos}")
                break
        return found

    def _skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1


def selects_something(matchers: list[Matcher]) -> bool:
    """Prometheus and Loki refuse selectors whose every matcher also matches the empty string."""
    return any(not m.matches({}) for m in matchers)


def parse_selector(query: str) -> list[Matcher]:
    """The matchers of a vector selector; a metric name becomes a __name__ matcher."""
    scanner = Scanner(query)
    found: list[Matcher] = []
    name = scanner.take(NAME)
    if name is not None:
        found.append(Matcher.make("__name__", "=", name))
    if not scanner.done() and scanner.text[scanner.pos] == "{":
        found += scanner.matchers()
    if not scanner.done():
        raise QueryError(f"unexpected {scanner.text[scanner.pos]!r} at position {scanner.pos}")
    if not selects_something(found):
        raise QueryError("a selector needs a metric name or a matcher that is not empty")
    return found
