"""Render the Whisper transcript to SRT or WebVTT.

We prefer the Whisper segments (one cue per segment). If the project has a
rough cut, we re-time the segments onto the rough-cut timeline so the captions
line up with the edited video.
"""

from __future__ import annotations

from typing import Iterable


def _ts_srt(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_vtt(seconds: float) -> str:
    return _ts_srt(seconds).replace(",", ".")


def render_srt(segments: Iterable[dict]) -> str:
    out: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start + 0.5)
        if end <= start:
            end = start + 0.5
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append(str(i))
        out.append(f"{_ts_srt(start)} --> {_ts_srt(end)}")
        out.append(text)
        out.append("")
    return "\n".join(out).strip() + "\n"


def render_vtt(segments: Iterable[dict]) -> str:
    out: list[str] = ["WEBVTT", ""]
    for seg in segments:
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start + 0.5)
        if end <= start:
            end = start + 0.5
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append(f"{_ts_vtt(start)} --> {_ts_vtt(end)}")
        out.append(text)
        out.append("")
    return "\n".join(out).strip() + "\n"


def retime_words(
    words: list[dict],
    keep_ranges: list[tuple[float, float]],
) -> list[dict]:
    """Same as retime_segments but for word-level entries with `word` text."""
    if not keep_ranges:
        return list(words)
    out: list[dict] = []
    cursor = 0.0
    for ks, ke in keep_ranges:
        for w in words:
            ws = float(w.get("start") or 0.0)
            we = float(w.get("end") or ws)
            if we <= ks or ws >= ke:
                continue
            cs = max(ks, ws)
            ce = min(ke, we)
            if ce <= cs:
                continue
            out.append({
                "word": w.get("word") or w.get("text") or "",
                "start": cursor + (cs - ks),
                "end": cursor + (ce - ks),
            })
        cursor += (ke - ks)
    return out


def retime_segments(
    segments: list[dict],
    keep_ranges: list[tuple[float, float]],
) -> list[dict]:
    """Map original segments onto the timeline produced by keep_ranges.

    Segments outside any keep range are dropped. Segments split across a
    boundary are clipped.
    """
    if not keep_ranges:
        return list(segments)

    out: list[dict] = []
    cursor = 0.0
    for ks, ke in keep_ranges:
        for seg in segments:
            ss = float(seg.get("start") or 0.0)
            se = float(seg.get("end") or ss)
            if se <= ks or ss >= ke:
                continue
            clipped_s = max(ks, ss)
            clipped_e = min(ke, se)
            new_s = cursor + (clipped_s - ks)
            new_e = cursor + (clipped_e - ks)
            out.append({
                "start": new_s,
                "end": new_e,
                "text": (seg.get("text") or "").strip(),
            })
        cursor += (ke - ks)
    return out
