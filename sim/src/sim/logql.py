"""The LogQL subset the sim answers: a stream selector, then line filters."""

import re
from dataclasses import dataclass

from sim.promql import Matcher, QueryError, Regex, Scanner, regex, selects_something

FILTER_OP = re.compile(r"\|=|!=|\|~|!~")


@dataclass(frozen=True)
class LineFilter:
    op: str
    value: str
    pattern: Regex | None

    def keeps(self, line: str) -> bool:
        """|= and != test for the text, |~ and !~ search for the regex anywhere in the line."""
        if self.pattern is None:
            return (self.value in line) == (self.op == "|=")
        return (self.pattern.search(line) is not None) == (self.op == "|~")


@dataclass(frozen=True)
class LogQuery:
    matchers: list[Matcher]
    filters: list[LineFilter]


def parse_log_query(query: str) -> LogQuery:
    """{service="checkout", level=~"warn|error"} |= "timeout" !~ "health" and the like."""
    scanner = Scanner(query)
    if scanner.done() or scanner.text[scanner.pos] != "{":
        raise QueryError('a log query starts with a stream selector such as {service="checkout"}')
    matchers = scanner.matchers()
    if not selects_something(matchers):
        raise QueryError("the stream selector needs at least one matcher that is not empty")
    filters: list[LineFilter] = []
    while not scanner.done():
        op = scanner.need(FILTER_OP, "a line filter: |=, !=, |~ or !~")
        value = scanner.string()
        filters.append(LineFilter(op, value, regex(value) if "~" in op else None))
    return LogQuery(matchers, filters)
