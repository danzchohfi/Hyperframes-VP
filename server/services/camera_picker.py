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
    speaker_per_interval: list[str | None] | None = None,
    speaker_to_cluster: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """For each interval, pick the best angle.

    Short intervals (< min_dur) inherit the previous pick to avoid flipping.
    """
    cuts: list[dict[str, Any]] = []
    last_angle: int | None = None
    for k, (s, e) in enumerate(intervals):
        if e - s < min_dur and last_angle is not None:
            cuts.append({"start": s, "end": e, "angle_index": last_angle, "reason": "hold"})
            continue

        speaker = (speaker_per_interval or [None] * len(intervals))[k]
        target_cluster = (speaker_to_cluster or {}).get(speaker) if speaker else None

        scored: list[tuple[int, float, str]] = []
        for i, a in enumerate(angles):
            base, reason = _score_for_interval(a, i)
            # Bonus when this angle has the target speaker's face cluster.
            if target_cluster:
                clusters_in_a = a.get("face_clusters") or []
                if target_cluster in clusters_in_a:
                    base += 4.0
                    reason += " · shows speaker"
                else:
                    base -= 1.0  # penalize cameras that DON'T show the speaker
            scored.append((i, base, reason))
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


def split_intervals_at_changes(
    intervals: list[tuple[float, float]],
    change_points: list[float],
    *,
    min_subspan: float = 1.5,
) -> list[tuple[float, float]]:
    """Subdivide each interval at any change point that falls inside it,
    keeping subspans >= min_subspan seconds. The result is fed back into
    pick_cameras so a long speaker turn can switch cameras when the subject
    changes mid-turn."""
    out: list[tuple[float, float]] = []
    cps = sorted({float(t) for t in change_points})
    for s, e in intervals:
        cuts_here = [t for t in cps if s + min_subspan <= t <= e - min_subspan]
        if not cuts_here:
            out.append((s, e))
            continue
        prev = s
        for t in cuts_here:
            out.append((prev, t))
            prev = t
        out.append((prev, e))
    return out
