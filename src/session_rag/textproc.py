"""Tokenizing and stemming for the full-text index (Russian and English Snowball stemmers)."""

from __future__ import annotations

import re
from functools import lru_cache

import snowballstemmer

_WORD = re.compile(r"[^\W_]+", re.U)
_CYR = re.compile(r"[а-яё]", re.I)
_RU = snowballstemmer.stemmer("russian")
_EN = snowballstemmer.stemmer("english")


@lru_cache(maxsize=200_000)
def stem(word: str) -> str:
    w = word.lower()
    if w.isdigit() or len(w) < 3:
        return w
    if _CYR.search(w):
        return _RU.stemWord(w)
    if w.isascii() and w.isalpha():
        return _EN.stemWord(w)
    return w


def words(text: str) -> list[str]:
    return [stem(m) for m in _WORD.findall(text)]


def stem_text(text: str) -> str:
    """Stemmed copy of a document for FTS (word order kept, so phrase queries still work)."""
    return " ".join(words(text))


_QUERY_TOKEN = re.compile(r"[\w.\-:/@]+", re.U)


def fts_query(q: str) -> str:
    """OR of phrases; a token like ``10.9.8.7`` or ``node-selector`` becomes a phrase of its stemmed parts."""
    phrases = []
    for tok in _QUERY_TOKEN.findall(q):
        parts = words(tok)
        if not parts or (len(parts) == 1 and len(parts[0]) < 2):
            continue
        phrases.append('"' + " ".join(parts) + '"')
    return " OR ".join(dict.fromkeys(phrases))
