"""Plan silence cuts: invert silence intervals into kept segments."""

from __future__ import annotations


def plan_keep_segments(
    duration: float,
    silences: list[tuple[float, float]],
    *,
    pad: float = 0.08,
    min_keep: float = 0.25,
) -> list[tuple[float, float]]:
    """Convert silence intervals → list of (start, end) speech segments to keep.

    pad: hold this many seconds of silence at each cut boundary so words don't clip.
    min_keep: drop micro-segments shorter than this.
    """
    if duration <= 0:
        return []
    if not silences:
        return [(0.0, duration)]

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
    return merged


def total_kept(segments: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in segments)
