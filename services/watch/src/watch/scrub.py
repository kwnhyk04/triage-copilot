"""The one scrubber: every log line, error and text that watch sends passes through it first.

A JSON log line keeps only its app's allowlisted fields. Every string is masked (keys, JWTs,
bearer tokens, PEM blocks, URL credentials, query strings, emails, tailnet names, assigned
secrets), a traceback keeps only its exception line, and fields and lines are truncated.
"""

import json
import re
from collections.abc import Iterable, Mapping
from typing import cast

from watch.catalog import Catalog

FIELD_CHARS = 500
LINE_CHARS = 1000
TRACEBACK = "Traceback (most recent call last):"

# In order: a PEM block first, since its body holds key-like text, and URL credentials before
# emails, which would otherwise take `user:pass@host` apart. The lookbehinds start a match only
# where a run of its characters starts, so a long run costs linear time, not quadratic.
_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?(?:-----END [A-Z0-9 ]+-----|$)", re.S), "[pem]"),
    (
        re.compile(
            r"\b(?:sk-ant-|nvapi-)[\w-]{8,}|\bAIza[\w-]{20,}|\bgsk_\w{20,}"
            r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_\w{12,}"
        ),
        "[key]",
    ),
    (re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*"), "[jwt]"),
    (re.compile(r"(?i)\b(bearer)\s+[\w.~+/=-]+"), r"\1 [token]"),
    (
        re.compile(r"(?i)(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s@]*@"),
        r"\1[credentials]@",
    ),
    (re.compile(r"(?<=[\w/.-])\?[^\s\"'<>]+"), "?[query]"),
    (re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?i)(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.ts\.net\b"), "[tailnet]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\"?\s*[=:]\s*\"?)[^\s,;&\"')}]+"
        ),
        r"\1\2[redacted]",
    ),
)


def mask(text: str) -> str:
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


def _exception_line(text: str) -> str:
    """A Python traceback's last line (its exception and message), or a Node stack's first."""
    if TRACEBACK in text:
        return next((line for line in reversed(text.splitlines()) if line.strip()), "")
    if "\n    at " in text:
        return text.split("\n", 1)[0]
    return text


def _value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # Masked before it is cut, so no key is cut short of its pattern.
    return mask(_exception_line(text))[:FIELD_CHARS]


def _keep(
    entry: Mapping[str, object], fields: frozenset[str], prefix: str = ""
) -> dict[str, object]:
    kept: dict[str, object] = {}
    for key, value in entry.items():
        path = prefix + key
        if path in fields:
            kept[key] = _value(value)
        elif (
            isinstance(value, dict)
            and any(field.startswith(f"{path}.") for field in fields)
            and (nested := _keep(cast("dict[str, object]", value), fields, f"{path}."))
        ):
            kept[key] = nested
    return kept


def scrub_line(catalog: Catalog, line: str) -> str:
    try:
        entry: object = json.loads(line)
    except ValueError:
        entry = None
    if isinstance(entry, dict):
        kept = _keep(cast("dict[str, object]", entry), catalog.log_fields)
        line = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
    else:
        line = mask(line)
    return line[:LINE_CHARS]


def scrub_lines(catalog: Catalog, lines: Iterable[str]) -> list[str]:
    """Each line scrubbed. A plain-text traceback's header and frames and Node's stack frames
    are dropped, so a traceback keeps only its exception line."""
    scrubbed: list[str] = []
    in_traceback = False
    for line in lines:
        indented = line[:1].isspace()
        if line.startswith(TRACEBACK):
            in_traceback = True
        elif not (indented and (in_traceback or line.lstrip().startswith("at "))):
            in_traceback = False
            scrubbed.append(scrub_line(catalog, line))
    return scrubbed
