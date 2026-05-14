"""Plan silence cuts: invert silence intervals into kept segments.

Two strategies live here:

1) `plan_keep_segments` (legacy, audio-level): uses `ff.detect_silences`
   output. Cuts anything below `noise_db` for >= `min_silence` seconds.
   Pitfall: a plane / fan / siren over the talk is "loud" → never marked
   as silence → never cut. Conversely a long dramatic pause is "silent"
   → cut even though it's intentional. The `sentence_safe` flag helps
   on the second case but doesn't address the first.

2) `plan_keep_from_speech` (NEW, transcript-driven): uses Whisper's
   word timestamps directly. The keep set is "everywhere there's a
   word, with breathing pad around it; gaps between words merge into
   one kept range as long as the gap is short." Non-speech audio
   (planes, music, room noise without dialogue) has NO words → big
   gap → cut. Pauses within a thought (<= `merge_gap`) stay kept so
   the cadence feels natural.
"""

from __future__ import annotations


def plan_keep_segments(
    duration: float,
    silences: list[tuple[float, float]],
    *,
    pad: float = 0.08,
    min_keep: float = 0.25,
    words: list[dict] | None = None,
    segments: list[dict] | None = None,
    sentence_safe: bool = False,
) -> list[tuple[float, float]]:
    """Convert silence intervals → list of (start, end) speech segments to keep.

    pad: hold this many seconds of silence at each cut boundary so words don't clip.
    min_keep: drop micro-segments shorter than this.

    sentence_safe + words: when set, discard silences whose midpoint falls
    inside a word (so we never chop syllables) and snap each keep range to
    the nearest word edge.
    """
    if duration <= 0:
        return []
    if not silences:
        return [(0.0, duration)]

    if sentence_safe and words:
        from . import speech_cuts as sc
        silences = sc.filter_silences_to_inter_sentence(silences, words, segments or [])

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

    if sentence_safe and words:
        from . import speech_cuts as sc
        merged = sc.snap_ranges(merged, words=words, segments=segments,
                                mode="word", pad=0.03)
    return merged


def total_kept(segments: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in segments)


def plan_keep_from_speech(
    duration: float,
    words: list[dict],
    *,
    pad: float = 0.18,
    merge_gap: float = 0.9,
    head_keep: float = 0.0,
    tail_keep: float = 0.0,
    min_keep: float = 0.4,
) -> list[tuple[float, float]]:
    """Build keep-ranges from Whisper word timestamps.

    Each word contributes [word.start - pad, word.end + pad] to the
    kept set. Adjacent intervals that touch or are within `merge_gap`
    seconds of each other get merged — that's what preserves natural
    speaking pauses (breath, dramatic stops). Anything past the last
    word + `tail_keep` and before the first word - `head_keep` is
    DISCARDED, which is exactly what cuts non-speech intros like a
    plane / room noise that the silencedetect-based path would miss.

    Args:
        duration: source duration in seconds (used to clamp ranges)
        words: list of {start, end, ...} from transcript.json
        pad: breathing space added on each side of every word
        merge_gap: if the gap between two padded words is <= this,
            merge them. Bigger gap = the pause likely isn't part of
            the conversation; cut it. Default 0.9s lets normal pauses
            through, cuts long off-topic gaps.
        head_keep / tail_keep: optional seconds of pre-/post-roll to
            preserve outside the first/last word. Default 0 (cut
            everything outside the dialogue).
        min_keep: drop merged ranges shorter than this — almost always
            a stray transcription bubble that wouldn't read well alone.

    Returns: list of (start, end) tuples in source time, clamped to
    [0, duration], non-overlapping, sorted.
    """
    if duration <= 0 or not words:
        return []

    # Build raw padded intervals.
    intervals: list[tuple[float, float]] = []
    for w in words:
        try:
            s = float(w["start"]) - pad
            e = float(w["end"]) + pad
        except (KeyError, TypeError, ValueError):
            continue
        if e <= s:
            continue
        intervals.append((max(0.0, s), min(duration, e)))
    if not intervals:
        return []
    intervals.sort()

    # Merge adjacent intervals whose gap is <= merge_gap.
    merged: list[tuple[float, float]] = [intervals[0]]
    for s, e in intervals[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e + merge_gap:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))

    # Optional head/tail preservation. Extend first interval LEFT by
    # head_keep up to (but not before) 0; last interval RIGHT by
    # tail_keep up to (but not past) duration. Without this the first
    # word starts at exactly word.start - pad, which can clip a sharp
    # consonant if the user wants a tiny lead-in.
    if head_keep > 0 and merged:
        s, e = merged[0]
        merged[0] = (max(0.0, s - head_keep), e)
    if tail_keep > 0 and merged:
        s, e = merged[-1]
        merged[-1] = (s, min(duration, e + tail_keep))

    # Drop micro-segments.
    merged = [(s, e) for s, e in merged if e - s >= min_keep]
    return merged
