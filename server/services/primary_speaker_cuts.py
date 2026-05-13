"""Build a silence-cut plan from speaker diarization.

The default silence detector (`services.silence.detect_silences`) uses a
fixed RMS threshold (~-32 dB), so it only cuts when the recording is
nearly empty. That misses the common case the user describes: the host
plus other people chatting in the background. The background voices are
quiet enough not to be soundbites but loud enough to clear the silence
threshold, so they survive the cut.

This module reuses an existing diarization (services.speakers, output
saved at `speakers.json`) to build a different kind of cut plan: keep
only the segments where the *primary* speaker is talking. Anything else
— silences, crosstalk, side conversations — gets dropped.

The output is written to `cuts.json` with the same schema as
detect_silences so the rest of the pipeline (cut_segments, FCPXML,
multicam) consumes it transparently.
"""

from __future__ import annotations

from typing import Any


def _pick_primary(turns: list[dict[str, Any]]) -> str | None:
    """Speaker with the most total speaking time."""
    totals: dict[str, float] = {}
    for t in turns:
        sp = str(t.get("speaker") or "")
        if not sp:
            continue
        totals[sp] = totals.get(sp, 0.0) + max(0.0, float(t.get("end") or 0.0) - float(t.get("start") or 0.0))
    if not totals:
        return None
    return max(totals, key=lambda k: totals[k])


def _merge_intervals(ivs: list[tuple[float, float]], *, gap_merge: float) -> list[tuple[float, float]]:
    if not ivs:
        return []
    ivs = sorted(ivs)
    out: list[list[float]] = [list(ivs[0])]
    for s, e in ivs[1:]:
        if s - out[-1][1] <= gap_merge:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def build_keep_plan(
    speakers_json: dict[str, Any],
    *,
    total_duration: float,
    padding: float = 0.15,
    gap_merge: float = 0.50,
    primary_speaker: str | None = None,
) -> dict[str, Any]:
    """Return {primary_speaker, segments: [{start, end}, ...]}.

    `segments` are the ranges to keep — same shape as the silence-cut
    plan. Background-only or other-speaker ranges are dropped.

    - padding: extra seconds before/after each turn so we don't clip
      the first/last word.
    - gap_merge: merge two kept ranges if the gap between them is
      smaller than this (avoids hundreds of micro-cuts on a fast-paced
      back-and-forth).
    """
    turns = list(speakers_json.get("turns") or [])
    if not turns:
        return {"primary_speaker": None, "segments": []}

    primary = primary_speaker or _pick_primary(turns)
    if not primary:
        return {"primary_speaker": None, "segments": []}

    raw: list[tuple[float, float]] = []
    for t in turns:
        if str(t.get("speaker") or "") != primary:
            continue
        s = max(0.0, float(t.get("start") or 0.0) - padding)
        e = float(t.get("end") or s) + padding
        if total_duration > 0:
            e = min(e, total_duration)
        if e > s:
            raw.append((s, e))

    merged = _merge_intervals(raw, gap_merge=gap_merge)
    keep = [{"start": round(s, 3), "end": round(e, 3)} for s, e in merged]
    kept_duration = round(sum(seg["end"] - seg["start"] for seg in keep), 3)
    return {
        "primary_speaker": primary,
        "keep": keep,
        "kept_duration": kept_duration,
        "source": "primary_speaker",
    }
