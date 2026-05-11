"""Rough-cut assembly: order soundbite ranges into a timeline plan.

The plan is consumed by `ffmpeg.cut_segments` (for an MP4) and by
`fcpxml.build_single_cam_fcpxml` / `premiere_xml.build_xmeml` (for NLE export).
"""

from __future__ import annotations

from typing import Any


def chapter_ranges(
    chapters: list[dict[str, Any]],
    soundbites: list[dict[str, Any]],
    *,
    pad: float = 0.05,
    words: list[dict] | None = None,
    segments: list[dict] | None = None,
) -> list[tuple[float, float]]:
    """Walk the chapters in order, look up each soundbite_id, return time ranges."""
    by_id = {sb["id"]: sb for sb in soundbites}
    ranges: list[tuple[float, float]] = []
    for ch in chapters:
        for sid in ch.get("soundbite_ids", []):
            sb = by_id.get(sid)
            if not sb:
                continue
            s = max(0.0, float(sb["start"]) - pad)
            e = float(sb["end"]) + pad
            ranges.append((s, e))
    if words:
        from . import speech_cuts as sc
        ranges = sc.snap_ranges(ranges, words=words, segments=segments, mode="word", pad=0.03)
    return ranges


def selected_ranges(
    soundbite_ids: list[str],
    soundbites: list[dict[str, Any]],
    *,
    pad: float = 0.05,
    words: list[dict] | None = None,
    segments: list[dict] | None = None,
) -> list[tuple[float, float]]:
    by_id = {sb["id"]: sb for sb in soundbites}
    out: list[tuple[float, float]] = []
    for sid in soundbite_ids:
        sb = by_id.get(sid)
        if not sb:
            continue
        out.append((max(0.0, float(sb["start"]) - pad), float(sb["end"]) + pad))
    if words:
        from . import speech_cuts as sc
        out = sc.snap_ranges(out, words=words, segments=segments, mode="word", pad=0.03)
    return out


def chapter_marker_plan(
    chapters: list[dict[str, Any]],
    soundbites: list[dict[str, Any]],
    *,
    pad: float = 0.05,
) -> list[dict[str, Any]]:
    """Returns chapter markers with timeline-relative offsets, suitable for FCPXML."""
    by_id = {sb["id"]: sb for sb in soundbites}
    markers: list[dict[str, Any]] = []
    cursor = 0.0
    for ch in chapters:
        chap_start = cursor
        for sid in ch.get("soundbite_ids", []):
            sb = by_id.get(sid)
            if not sb:
                continue
            cursor += (float(sb["end"]) + pad) - max(0.0, float(sb["start"]) - pad)
        if cursor > chap_start:
            markers.append({
                "name": ch.get("name") or ch.get("id") or "Chapter",
                "start": chap_start,
                "duration": cursor - chap_start,
            })
    return markers
