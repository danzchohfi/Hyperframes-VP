"""Pick top-scoring soundbites totaling ~target_seconds and order them
chronologically. Returns the ranges to feed into ffmpeg.cut_segments.
"""

from __future__ import annotations


def select(soundbites: list[dict], *, target_seconds: float = 30.0) -> list[tuple[float, float]]:
    if not soundbites:
        return []
    by_score = sorted(soundbites, key=lambda b: -float(b.get("score") or 0))
    chosen: list[dict] = []
    total = 0.0
    for sb in by_score:
        dur = float(sb.get("end", 0)) - float(sb.get("start", 0))
        if dur <= 0:
            continue
        if total + dur > target_seconds * 1.15 and chosen:
            continue
        chosen.append(sb)
        total += dur
        if total >= target_seconds:
            break
    chosen.sort(key=lambda b: float(b.get("start") or 0))
    return [(float(b["start"]), float(b["end"])) for b in chosen]
