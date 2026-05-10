"""Decide which camera to cut to at each speaker turn (or each soundbite).

Given a list of angles (with face_analysis stats + audio offsets) and a list
of intervals to cover (turns or bites), produce a cut list:

  [{start, end, angle_index, reason}, ...]

Heuristic ranking per interval:
  +3.0 if the angle has a face in close_up
  +1.5 if medium shot
  +0.5 if wide shot (with someone in it)
  -1.0 if no face at all
  +0.5 if the angle's face center is near the middle (well-framed)
  +0.5 if the angle is the source (often the primary wide / safety shot)
  -1.5 if quality_check says blurry/shaky/dark

The result avoids rapid camera-flipping: turns shorter than `min_dur`
inherit the previous camera.
"""

from __future__ import annotations

from typing import Any


def _score_for_interval(angle: dict[str, Any], i: int) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    fa = angle.get("face_analysis") or {}
    shot = fa.get("shot_type", "no_face")
    if shot == "close_up":
        score += 3.0; reasons.append("close-up")
    elif shot == "medium":
        score += 1.5; reasons.append("medium")
    elif shot == "wide":
        score += 0.5; reasons.append("wide")
    else:
        score -= 1.0; reasons.append("no face")

    # Well-framed (face near center)
    fx = float(fa.get("face_x_avg") or 0.5)
    if 0.35 <= fx <= 0.65 and shot != "no_face":
        score += 0.5; reasons.append("centered")

    # Source / camera-0 bias (often the safety shot)
    if i == 0:
        score += 0.5; reasons.append("primary")

    # Quality
    qc = angle.get("quality_check") or {}
    q = qc.get("quality")
    if q and q != "ok":
        score -= 1.5; reasons.append(f"q={q}")

    return score, " · ".join(reasons)


def pick_cameras(
    angles: list[dict[str, Any]],
    intervals: list[tuple[float, float]],
    *,
    min_dur: float = 1.4,
) -> list[dict[str, Any]]:
    """For each interval, pick the best angle.

    Short intervals (< min_dur) inherit the previous pick to avoid flipping.
    """
    cuts: list[dict[str, Any]] = []
    last_angle: int | None = None
    for s, e in intervals:
        if e - s < min_dur and last_angle is not None:
            cuts.append({"start": s, "end": e, "angle_index": last_angle, "reason": "hold"})
            continue
        # Rank angles
        scored = [(i, *_score_for_interval(a, i)) for i, a in enumerate(angles)]
        scored.sort(key=lambda x: -x[1])
        best_idx, best_score, best_reason = scored[0]
        cuts.append({
            "start": s,
            "end": e,
            "angle_index": best_idx,
            "score": round(best_score, 2),
            "reason": best_reason,
        })
        last_angle = best_idx
    return cuts


def merge_adjacent(cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive cuts pointing at the same angle into a single span."""
    if not cuts:
        return cuts
    out = [dict(cuts[0])]
    for c in cuts[1:]:
        if c["angle_index"] == out[-1]["angle_index"]:
            out[-1]["end"] = c["end"]
        else:
            out.append(dict(c))
    return out
