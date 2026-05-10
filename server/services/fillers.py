"""Filler-word detection on a Whisper transcript.

Returns time ranges to drop. The cut planner then subtracts these ranges
from the silence-cut keep plan, so a single `apply` pass removes both.
"""

from __future__ import annotations

import re

DEFAULT_FILLERS_PT = {
    # interjections
    "ã", "ãh", "ah", "ahn", "uhm", "hum", "hm", "hmm", "é", "éh", "eh",
    # crutches
    "tipo", "né", "tá", "então", "sabe", "cara", "mano", "tipo assim",
    "como assim", "entendeu",
}
DEFAULT_FILLERS_EN = {
    "um", "uh", "uhm", "hmm", "ah", "er", "you know", "like",
    "i mean", "so", "well", "right",
}
PUNCT_RE = re.compile(r"[\.,!\?;:\"'\-—–…]+")


def _normalize(word: str) -> str:
    return PUNCT_RE.sub("", word.strip()).lower()


def filler_set(language: str | None, custom: list[str] | None = None) -> set[str]:
    lang = (language or "auto").lower()
    if lang.startswith("pt"):
        base = set(DEFAULT_FILLERS_PT)
    elif lang.startswith("en"):
        base = set(DEFAULT_FILLERS_EN)
    else:
        base = set(DEFAULT_FILLERS_PT) | set(DEFAULT_FILLERS_EN)
    if custom:
        base |= {_normalize(c) for c in custom if c}
    return base


def detect_filler_ranges(
    words: list[dict],
    *,
    language: str | None = "auto",
    custom: list[str] | None = None,
    pad: float = 0.04,
) -> list[tuple[float, float]]:
    """Return (start, end) seconds for each filler-word occurrence.

    Multi-word fillers (e.g. "you know") are detected across consecutive words.
    """
    fillers = filler_set(language, custom)
    multi = sorted([f for f in fillers if " " in f], key=lambda x: -x.count(" "))
    single = {f for f in fillers if " " not in f}

    n = len(words)
    ranges: list[tuple[float, float]] = []
    i = 0
    while i < n:
        w = words[i]
        text = _normalize(w.get("word", ""))
        matched_len = 0

        # try multi-word fillers first
        for phrase in multi:
            parts = phrase.split()
            tail = [_normalize(words[j].get("word", "")) for j in range(i, min(i + len(parts), n))]
            if tail == parts:
                matched_len = len(parts)
                break

        if matched_len == 0 and text in single:
            matched_len = 1

        if matched_len:
            s = max(0.0, float(words[i]["start"]) - pad)
            e = float(words[i + matched_len - 1]["end"]) + pad
            ranges.append((s, e))
            i += matched_len
        else:
            i += 1

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


def subtract_ranges(
    keep: list[tuple[float, float]],
    drop: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Subtract `drop` intervals from `keep` (both sorted, non-overlapping)."""
    if not drop:
        return keep
    out: list[tuple[float, float]] = []
    for ks, ke in keep:
        cursor = ks
        for ds, de in drop:
            if de <= ks or ds >= ke:
                continue
            if ds > cursor:
                out.append((cursor, min(ds, ke)))
            cursor = max(cursor, de)
            if cursor >= ke:
                break
        if cursor < ke:
            out.append((cursor, ke))
    # clean tiny remnants and merge
    cleaned = [(s, e) for s, e in out if e - s >= 0.05]
    return cleaned


def stats(ranges: list[tuple[float, float]]) -> dict:
    return {
        "count": len(ranges),
        "duration": round(sum(e - s for s, e in ranges), 3),
    }
