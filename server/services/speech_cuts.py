"""Speech-aware cut boundaries.

When we cut video on times derived from a transcript or silence detector,
those times may fall mid-word or mid-sentence. This module rounds an
arbitrary time to the nearest legitimate boundary:

  - word boundary: start or end of a word, with a small grace gap
  - sentence boundary: end of a Whisper segment, or after a word ending
    in `. ! ?` followed by a >= ~0.35s pause

Functions are intentionally pure so they can be invoked from any cut path
(silence cuts, soundbite ranges, vlog assembly, filler subtraction).
"""

from __future__ import annotations

import re
from typing import Iterable, Literal


Direction = Literal["nearest", "before", "after"]
SENTENCE_PUNCT_RE = re.compile(r"[\.!\?…]+$")


def _word_pause_after(words: list[dict], i: int) -> float:
    """Gap (seconds) between the i-th word's end and the next word's start."""
    if i + 1 >= len(words):
        return 9_999.0
    return max(0.0, float(words[i + 1]["start"]) - float(words[i]["end"]))


def collect_word_edges(words: Iterable[dict]) -> list[tuple[float, str]]:
    """Returns sorted (time, kind) tuples where kind ∈ {"word_start", "word_end"}."""
    edges: list[tuple[float, str]] = []
    for w in words:
        edges.append((float(w["start"]), "word_start"))
        edges.append((float(w["end"]), "word_end"))
    edges.sort()
    return edges


def collect_sentence_boundaries(
    words: list[dict],
    segments: list[dict],
    *,
    min_sentence_pause: float = 0.35,
) -> list[float]:
    """Hard sentence breaks: end-of-Whisper-segment, OR end-of-punctuated-word
    followed by a >= min_sentence_pause silence."""
    out: list[float] = []
    for seg in segments or []:
        try:
            out.append(float(seg.get("end") or 0.0))
        except Exception:
            continue
    for i, w in enumerate(words or []):
        txt = (w.get("word") or "").strip()
        if SENTENCE_PUNCT_RE.search(txt):
            if _word_pause_after(words, i) >= min_sentence_pause:
                out.append(float(w["end"]))
    # dedupe + sort
    return sorted({round(t, 3) for t in out if t > 0})


def snap_to_word_boundary(
    t: float,
    words: list[dict],
    *,
    direction: Direction = "nearest",
    max_drift: float = 0.45,
) -> float:
    """Round `t` to the nearest word edge. Returns the original `t` if no
    edge is found within `max_drift`."""
    if not words:
        return t
    edges = collect_word_edges(words)
    # binary search would be O(log n); these lists are short
    best: tuple[float, float] | None = None
    for et, kind in edges:
        if direction == "before" and et > t:
            continue
        if direction == "after" and et < t:
            continue
        d = abs(et - t)
        if best is None or d < best[0]:
            best = (d, et)
    if best is None or best[0] > max_drift:
        return t
    return best[1]


def snap_to_sentence_boundary(
    t: float,
    words: list[dict],
    segments: list[dict],
    *,
    direction: Direction = "nearest",
    max_drift: float = 1.2,
) -> float:
    """Round to the nearest sentence boundary. Falls back to word boundary
    if no sentence break is found within `max_drift`."""
    boundaries = collect_sentence_boundaries(words, segments)
    if boundaries:
        best: tuple[float, float] | None = None
        for b in boundaries:
            if direction == "before" and b > t:
                continue
            if direction == "after" and b < t:
                continue
            d = abs(b - t)
            if best is None or d < best[0]:
                best = (d, b)
        if best is not None and best[0] <= max_drift:
            return best[1]
    return snap_to_word_boundary(t, words, direction=direction, max_drift=max_drift)


def is_inside_word(t: float, words: list[dict], *, margin: float = 0.04) -> bool:
    """True if `t` falls strictly inside the (start+margin, end-margin) of a word.
    Used to bail out of cuts that would chop a syllable."""
    for w in words or []:
        s = float(w["start"]) + margin
        e = float(w["end"]) - margin
        if s < e and s <= t <= e:
            return True
    return False


def snap_range(
    start: float,
    end: float,
    *,
    words: list[dict],
    segments: list[dict] | None = None,
    mode: Literal["word", "sentence"] = "word",
    pad: float = 0.05,
) -> tuple[float, float]:
    """Snap (start, end) to clean boundaries. Start rolls backward (so we
    don't lose the first syllable); end rolls forward (so we don't clip the
    last word). Falls back to the original time if no boundary nearby."""
    segments = segments or []
    if mode == "sentence" and segments:
        ns = snap_to_sentence_boundary(start, words, segments, direction="before")
        ne = snap_to_sentence_boundary(end, words, segments, direction="after")
    else:
        ns = snap_to_word_boundary(start, words, direction="before")
        ne = snap_to_word_boundary(end, words, direction="after")
    # Allow a small pad outside the boundary so transients aren't clipped.
    return max(0.0, ns - pad), max(ns + 0.04, ne + pad)


def snap_ranges(
    ranges: list[tuple[float, float]],
    *,
    words: list[dict],
    segments: list[dict] | None = None,
    mode: Literal["word", "sentence"] = "word",
    pad: float = 0.05,
) -> list[tuple[float, float]]:
    out = [snap_range(s, e, words=words, segments=segments, mode=mode, pad=pad) for s, e in ranges]
    return _merge(out)


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


def filter_silences_to_inter_sentence(
    silences: list[tuple[float, float]],
    words: list[dict],
    segments: list[dict],
    *,
    min_silence: float = 0.5,
    safety_inside_sentence: float = 0.06,
) -> list[tuple[float, float]]:
    """Keep only silences that fall BETWEEN words, not inside a word.

    A silence is "inside a sentence" when its start or end lands inside a
    word boundary (within `safety_inside_sentence` seconds). We drop those.
    A silence remains a candidate for cutting if its midpoint is OUTSIDE
    every word's [start, end] window.
    """
    out: list[tuple[float, float]] = []
    for s, e in silences:
        if e - s < min_silence:
            continue
        mid = (s + e) / 2.0
        chops_word = False
        for w in words:
            ws = float(w["start"])
            we = float(w["end"])
            if ws + safety_inside_sentence <= mid <= we - safety_inside_sentence:
                chops_word = True
                break
        if not chops_word:
            out.append((s, e))
    return out
