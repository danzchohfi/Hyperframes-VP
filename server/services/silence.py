"""Plan silence cuts: invert silence intervals into kept segments."""

from __future__ import annotations


def plan_keep_segments(
    duration: float,
    silences: list[tuple[float, float]],
    *,
    pad: float = 0.08,
    min_keep: float = 0.25,
    words: list[dict] | None = None,
    segments: list[dict] | None = None,
    sentence_safe: bool = False,
) -> list[tuple[float, float]]:
    """Convert silence intervals → list of (start, end) speech segments to keep.

    pad: hold this many seconds of silence at each cut boundary so words don't clip.
    min_keep: drop micro-segments shorter than this.

    sentence_safe + words: when set, discard silences whose midpoint falls
    inside a word (so we never chop syllables) and snap each keep range to
    the nearest word edge.
    """
    if duration <= 0:
        return []
    if not silences:
        return [(0.0, duration)]

    if sentence_safe and words:
        from . import speech_cuts as sc
        silences = sc.filter_silences_to_inter_sentence(silences, words, segments or [])

    kept: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in silences:
        keep_end = max(cursor, s_start - 0.0) + pad
        keep_end = min(keep_end, duration)
        if keep_end - cursor >= min_keep:
            kept.append((cursor, keep_end))
        cursor = max(0.0, s_end - pad)
    if duration - cursor >= min_keep:
        kept.append((cursor, duration))

    # merge overlapping / adjacent (gap < pad*2)
    merged: list[tuple[float, float]] = []
    for s, e in kept:
        if merged and s <= merged[-1][1] + pad * 2:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    if sentence_safe and words:
        from . import speech_cuts as sc
        merged = sc.snap_ranges(merged, words=words, segments=segments,
                                mode="word", pad=0.03)
    return merged


def total_kept(segments: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in segments)
