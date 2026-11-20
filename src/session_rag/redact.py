"""Mask secrets before anything is written to the index.

Best effort: covers common key formats and key=value / JSON / URL credentials. It lowers the
risk of a secret surfacing in search results; it does not guarantee that every secret is caught.
"""

from __future__ import annotations

import re

MASK = "***"

# (pattern, keep_groups): keep_groups are group numbers kept verbatim around the masked part
_KEYWORDS = (
    r"(?:pass(?:word|wd|phrase)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|credentials?|pwd|auth[_-]?key|dsn|salt)"
)

_RULES: list[tuple[re.Pattern[str], tuple[int, ...]]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), ()),
    (re.compile(r"AGE-SECRET-KEY-[0-9A-Z]+"), ()),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), ()),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), ()),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}\b"), ()),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), ()),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"), ()),
    (re.compile(r"\bsk-(?:ant-|proj-|live-)?[A-Za-z0-9_\-]{20,}\b"), ()),
    (re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}\b"), ()),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b"), ()),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), ()),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), ()),
    (re.compile(r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=\-]{16,}"), (1,)),
    # key = value, "key": "value", KEY: value
    (
        re.compile(
            rf"""(?ix)
            (\b[A-Za-z0-9_\-]*{_KEYWORDS}[A-Za-z0-9_\-]*["']?\s*[:=]\s*["']?)
            (?!\*\*\*)([^\s"',;}}{{<>]{{6,}})"""
        ),
        (1,),
    ),
    # --password value / token value: only when the value looks generated (has a digit)
    (
        re.compile(
            rf"""(?ix)
            (\b-{{0,2}}[A-Za-z0-9_\-]*{_KEYWORDS}[A-Za-z0-9_\-]*\s+["']?)
            (?!\*\*\*)(?=[^\s"']*\d)([^\s"',;}}{{<>]{{8,}})"""
        ),
        (1,),
    ),
    (re.compile(r'(?i)("(?:key|secret|value)"\s*:\s*")([A-Za-z0-9/+=_\-]{16,})'), (1,)),
    # credentials in URLs: scheme://user:pass@host
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:)([^\s@/]{3,})(@)"), (1, 3)),
]


def redact(text: str) -> str:
    for pattern, keep in _RULES:
        if not keep:
            text = pattern.sub(MASK, text)
        else:
            text = pattern.sub(lambda m, keep=keep: _rebuild(m, keep), text)
    return text


def _rebuild(m: re.Match[str], keep: tuple[int, ...]) -> str:
    before = m.group(keep[0])
    after = m.group(keep[1]) if len(keep) > 1 else ""
    return before + MASK + after
