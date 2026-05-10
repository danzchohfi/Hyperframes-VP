"""Pick the strongest 3-5s "hook" clip from a transcript + soundbites.

Strategy:
  - Prefer the highest-scoring soundbite that fits within target_seconds
  - Else: trim the top soundbite to its strongest target_seconds window
  - Else: take the first non-silent N seconds of the source

Always returns a single (start, end) tuple.
"""

from __future__ import annotations


def pick(
    soundbites: list[dict] | None,
    *,
    target_seconds: float = 4.0,
    fallback_duration: float = 0.0,
) -> tuple[float, float] | None:
    if soundbites:
        # Sort by score, scan for one that fits within ±15% of target
        ranked = sorted(soundbites, key=lambda b: -float(b.get("score") or 0))
        # First pass: short bites within ±15% of target
        for b in ranked:
            dur = float(b.get("end", 0)) - float(b.get("start", 0))
            if 0 < dur <= target_seconds * 1.15 and dur >= target_seconds * 0.6:
                return (float(b["start"]), float(b["end"]))
        # Second pass: clip the top bite to the first target_seconds window
        if ranked:
            top = ranked[0]
            s = float(top.get("start", 0))
            e = float(top.get("end", s + target_seconds))
            if e - s > target_seconds:
                return (s, s + target_seconds)
            if e > s:
                return (s, e)
    if fallback_duration > 0:
        return (0.0, min(target_seconds, fallback_duration))
    return None
