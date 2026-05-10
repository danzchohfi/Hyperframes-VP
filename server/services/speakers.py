"""Lightweight speaker-turn segmentation from a Whisper transcript.

We don't have proper diarization — we approximate "speaker turn changes" by
looking for long pauses between consecutive words. This is enough for
single-interviewee content and gives a rough Speaker A / Speaker B split.

For real diarization, plug in pyannote.audio later.
"""

from __future__ import annotations

from typing import Any


def segment_turns(words: list[dict], *, gap_threshold: float = 1.2) -> list[dict[str, Any]]:
    """Walk the words, start a new turn whenever the inter-word gap exceeds
    `gap_threshold` seconds. Speakers alternate A/B by default.

    Returns: [{speaker: "A"|"B", start, end, text}, ...]
    """
    if not words:
        return []
    turns: list[dict[str, Any]] = []
    speaker_idx = 0
    cur: list[dict[str, Any]] = [words[0]]
    for prev, w in zip(words, words[1:]):
        gap = float(w.get("start") or 0.0) - float(prev.get("end") or 0.0)
        if gap >= gap_threshold and len(cur) > 0:
            turns.append(_finalize(cur, speaker_idx))
            speaker_idx ^= 1
            cur = [w]
        else:
            cur.append(w)
    if cur:
        turns.append(_finalize(cur, speaker_idx))
    return turns


def _finalize(group: list[dict[str, Any]], speaker_idx: int) -> dict[str, Any]:
    return {
        "speaker": "A" if speaker_idx == 0 else "B",
        "start": float(group[0].get("start") or 0.0),
        "end": float(group[-1].get("end") or 0.0),
        "text": " ".join((g.get("word") or "").strip() for g in group).strip(),
        "word_count": len(group),
    }
