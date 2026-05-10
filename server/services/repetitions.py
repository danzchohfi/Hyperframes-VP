"""Detect repetitive phrases in a transcript.

Two cheap signals:
  1. Word-level immediate repetition: "I I I" or "the the".
  2. N-gram repetition across segments: same 3-5 word sequence appearing
     twice within a short window.

Returns ranges to cut, suitable for merging with filler ranges.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Iterable


_PUNCT = re.compile(r"[\.,!\?;:\"'\-—–…]+")


def _norm(word: str) -> str:
    return _PUNCT.sub("", word.strip()).lower()


def immediate_repetitions(words: list[dict], *, pad: float = 0.04) -> list[tuple[float, float]]:
    """Detect "X X X" runs and return ranges to cut for all but the last X."""
    ranges: list[tuple[float, float]] = []
    i = 0
    while i < len(words):
        wnorm = _norm(words[i].get("word", ""))
        if not wnorm:
            i += 1
            continue
        j = i + 1
        while j < len(words) and _norm(words[j].get("word", "")) == wnorm:
            j += 1
        run = j - i
        if run > 1:
            # cut all but the last occurrence
            for k in range(i, j - 1):
                s = max(0.0, float(words[k]["start"]) - pad)
                e = float(words[k]["end"]) + pad
                ranges.append((s, e))
        i = j if run > 1 else i + 1
    return _merge(ranges)


def ngram_repetitions(
    words: list[dict],
    *,
    n: int = 4,
    window_seconds: float = 12.0,
    pad: float = 0.04,
) -> list[tuple[float, float]]:
    """Find n-grams that repeat within `window_seconds`. Cut the SECOND
    occurrence."""
    tokens = [(_norm(w.get("word", "")), w) for w in words]
    tokens = [(n_, w) for n_, w in tokens if n_]
    if len(tokens) < n:
        return []
    ranges: list[tuple[float, float]] = []
    seen: dict[tuple[str, ...], float] = {}  # ngram → end_time of last occurrence
    for i in range(len(tokens) - n + 1):
        gram = tuple(tokens[i + k][0] for k in range(n))
        start_w = tokens[i][1]
        end_w = tokens[i + n - 1][1]
        gram_start = float(start_w["start"])
        gram_end = float(end_w["end"])
        last_end = seen.get(gram)
        if last_end is not None and (gram_start - last_end) <= window_seconds:
            # cut this occurrence
            ranges.append((max(0.0, gram_start - pad), gram_end + pad))
        seen[gram] = gram_end
    return _merge(ranges)


def _merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not ranges:
        return []
    ranges = sorted(ranges)
    out = [ranges[0]]
    for s, e in ranges[1:]:
        if s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def stats(ranges: list[tuple[float, float]]) -> dict:
    return {
        "count": len(ranges),
        "duration": round(sum(e - s for s, e in ranges), 3),
    }
