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


def attach_speakers(segments: list[dict], turns: list[dict]) -> list[dict]:
    """Tag each segment with the speaker whose turn covers its midpoint."""
    if not turns:
        return segments
    out: list[dict] = []
    for seg in segments:
        mid = (float(seg.get("start") or 0.0) + float(seg.get("end") or 0.0)) / 2.0
        sp = None
        for t in turns:
            if mid >= float(t.get("start") or 0.0) and mid <= float(t.get("end") or 0.0):
                sp = t.get("speaker")
                break
        if sp is None:
            out.append(seg)
        else:
            text = (seg.get("text") or "").strip()
            out.append({**seg, "text": f"[Speaker {sp}] {text}"})
    return out


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


def _ts_ass(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - (h * 3600 + m * 60)
    return f"{h}:{m:02d}:{s:05.2f}"


def render_ass(
    segments: Iterable[dict],
    *,
    style: str = "minimal",
    width: int = 1080,
    height: int = 1920,
    primary: str = "&H00FFFFFF",   # ASS BGR (white)
    highlight: str = "&H0070DBFC",  # tiktok-ish yellow
    font_family: str = "Inter",
) -> str:
    """ASS subtitles. If `segments` carry a `words` list each with start/end,
    we emit `\\k` karaoke timing tags so the active word highlights live."""
    if style == "tiktok":
        font_size = int(height * 0.055)
        border = 5
        weight = -1   # bold
    elif style == "podcast":
        font_size = int(height * 0.038)
        border = 3
        weight = 0
    else:  # minimal
        font_size = int(height * 0.045)
        border = 4
        weight = 0

    ass = []
    ass.append("[Script Info]")
    ass.append("ScriptType: v4.00+")
    ass.append(f"PlayResX: {width}")
    ass.append(f"PlayResY: {height}")
    ass.append("WrapStyle: 0")
    ass.append("ScaledBorderAndShadow: yes")
    ass.append("")
    ass.append("[V4+ Styles]")
    ass.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    ass.append(
        f"Style: Default,{font_family},{font_size},{primary},{highlight},&H00000000,&H80000000,"
        f"{1 if weight else 0},0,0,0,100,100,0,0,1,{border},2,2,80,80,180,1"
    )
    ass.append("")
    ass.append("[Events]")
    ass.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    for seg in segments:
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start + 0.5)
        if end <= start:
            end = start + 0.5
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        # If seg has per-word timings, emit karaoke; else plain text
        words = seg.get("words")
        if words:
            parts = []
            for w in words:
                ws = float(w.get("start") or start)
                we = float(w.get("end") or ws + 0.2)
                cs = max(0.0, we - ws)
                centiseconds = max(1, int(round(cs * 100)))
                wt = (w.get("word") or w.get("text") or "").strip()
                if not wt:
                    continue
                parts.append(f"{{\\kf{centiseconds}}}{wt}")
            text_ass = " ".join(parts)
        else:
            text_ass = text.replace("\n", "\\N")
        layer = "0"
        ass.append(
            f"Dialogue: {layer},{_ts_ass(start)},{_ts_ass(end)},Default,,0,0,0,,{text_ass}"
        )
    return "\n".join(ass) + "\n"


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
            # preserve any extra fields (speaker, word_count, etc.)
            extra = {k: v for k, v in seg.items() if k not in ("start", "end", "text")}
            out.append({**extra, "start": new_s, "end": new_e,
                        "text": (seg.get("text") or "").strip()})
        cursor += (ke - ks)
    return out
