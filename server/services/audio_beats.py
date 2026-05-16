"""Beat + onset detection for audio-reactive animation timing.

Given a source audio (or video) path, returns a dictionary
`{bpm, beats: [t_seconds, ...], onsets: [t_seconds, ...]}`.

The two lists are different: `beats` is the inferred steady pulse
(tempo grid, usually drums/percussion), while `onsets` is every
detected energy spike (consonant attacks, instrument hits, percussion).
For animation snapping, beats give a musical pulse and onsets catch
non-pulsed accents (great for podcasts where there's no music).

librosa is heavy (numba, audioread) so we import it lazily — callers
get a `RuntimeError` with a clear message when it's missing rather
than killing the whole server at boot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from . import ffmpeg as ff

log = logging.getLogger(__name__)


# librosa wants 22050 Hz mono PCM for best beat-tracker accuracy. Higher
# rates buy nothing for sub-1 kHz drum/bass content; lower rates start
# to miss attacks on hi-hats. mono because beat tracking ignores stereo.
BEAT_AUDIO_RATE = 22050


def _cache_dir() -> Path:
    """~/.cache/hfvp/beats — same convention as Whisper. Beat extraction
    on a 30-min podcast is a few seconds, but caching makes the planner
    flow snappy when the user iterates on prompts."""
    root = Path(os.environ.get("HFVP_CACHE_DIR") or (Path.home() / ".cache" / "hfvp"))
    d = root / "beats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_librosa():
    """Lazy import — librosa pulls in numba which is slow to import and
    not needed unless the user actually asks for beats."""
    try:
        import librosa  # type: ignore
        return librosa
    except ImportError as e:
        raise RuntimeError(
            "librosa não está instalado. Rode: "
            "pip install librosa>=0.10 audioread"
        ) from e


async def _to_beat_audio(src: Path, dst: Path) -> None:
    """Re-encode any input (video/audio) to 22050 Hz mono WAV for
    librosa. WAV (PCM) is what librosa expects; MP3 would force a
    second decode pass through audioread."""
    await ff.run([
        "ffmpeg", "-y",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", str(BEAT_AUDIO_RATE),
        "-c:a", "pcm_s16le",
        str(dst),
    ])


def _run_librosa(wav_path: Path) -> dict[str, Any]:
    """Synchronous librosa work — beats + onsets. Run inside a thread
    when called from async code."""
    librosa = _ensure_librosa()
    y, sr = librosa.load(str(wav_path), sr=BEAT_AUDIO_RATE, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beats = [round(float(t), 3) for t in librosa.frames_to_time(beat_frames, sr=sr)]

    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units="frames")
    onsets = [round(float(t), 3) for t in librosa.frames_to_time(onset_frames, sr=sr)]

    return {
        "bpm": round(float(tempo), 2) if tempo else 0.0,
        "duration": round(duration, 3),
        "beats": beats,
        "onsets": onsets,
        "sr": sr,
    }


async def detect_beats(src: Path, *, project_dir: Path | None = None) -> dict[str, Any]:
    """Detect beats + onsets for `src`. Cached by sha1(src) so re-runs
    are instant. When `project_dir` is given, the result is also
    written to `project_dir / 'audio_beats.json'` for downstream
    pipelines (anim_planner reads from there)."""
    cache_path = _cache_dir() / f"{_hash_file(src)}.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
            log.info("audio_beats: cache hit %s", cache_path.name)
            if project_dir is not None:
                (project_dir / "audio_beats.json").write_text(json.dumps(data))
            return data
        except Exception as e:
            log.warning("audio_beats: corrupt cache %s: %s", cache_path, e)

    workdir = Path(tempfile.mkdtemp(prefix="hfvp_beats_"))
    try:
        wav = workdir / "audio.wav"
        await _to_beat_audio(src, wav)

        import asyncio
        data = await asyncio.to_thread(_run_librosa, wav)

        try:
            cache_path.write_text(json.dumps(data))
        except Exception as e:
            log.warning("audio_beats: cache write failed: %s", e)
        if project_dir is not None:
            (project_dir / "audio_beats.json").write_text(json.dumps(data))
        log.info(
            "audio_beats: %.1fs · bpm=%.1f · %d beats · %d onsets",
            data.get("duration", 0.0), data.get("bpm", 0.0),
            len(data.get("beats") or []), len(data.get("onsets") or []),
        )
        return data
    finally:
        try:
            for p in workdir.rglob("*"):
                if p.is_file():
                    p.unlink()
            workdir.rmdir()
        except Exception:
            pass


def snap_to_beat(t: float, beats: list[float], *, window: float = 0.15) -> float:
    """Return the nearest beat to `t` if it's within ±window seconds,
    otherwise return `t` unchanged. Used to align animation starts
    with the music's pulse without overriding intentional placements."""
    if not beats:
        return t
    # binary search would be O(log n) but the lists are short enough
    # (a few hundred entries for a long podcast) that a linear scan
    # is simpler and just as fast in practice.
    best = t
    best_dt = window
    for b in beats:
        if b < t - window:
            continue
        if b > t + window:
            break
        dt = abs(b - t)
        if dt <= best_dt:
            best = b
            best_dt = dt
    return round(best, 3)


def snap_animations_to_beats(
    animations: list[dict[str, Any]],
    beats_data: dict[str, Any] | None,
    *,
    window: float = 0.15,
) -> list[dict[str, Any]]:
    """Walk an animation plan and snap each `start` to the nearest
    beat (or onset, as fallback) within ±window. Mutates a copy;
    leaves the originals untouched.

    Snapping is applied to ALL animation types — the LLM is already
    instructed not to place hooks/CTAs mid-stream, so snapping the
    hook intro to the first beat just tightens the entrance feel.
    """
    if not beats_data:
        return animations
    beats = list(beats_data.get("beats") or [])
    onsets = list(beats_data.get("onsets") or [])
    out: list[dict[str, Any]] = []
    for a in animations:
        a2 = dict(a)
        original = float(a2.get("start") or 0.0)
        snapped = snap_to_beat(original, beats, window=window)
        if snapped == original and onsets:
            # Fall back to onsets when no beat is close — onsets catch
            # speech accents that the beat tracker ignores.
            snapped = snap_to_beat(original, onsets, window=window)
        if snapped != original:
            a2["start"] = snapped
            a2["snapped_from"] = round(original, 3)
        out.append(a2)
    return out


def beats_context_block(
    beats_data: dict[str, Any] | None,
    *,
    chapters: list[dict[str, Any]] | None = None,
    soundbites: list[dict[str, Any]] | None = None,
    max_beats: int = 50,
) -> str:
    """Format a `<beats>` context string for the planner LLM.

    Strategy: include the BPM (so the model can match the energy) and
    a curated list of up to `max_beats` beats. When chapters and
    soundbites are available, we bias toward beats near those moments
    — those are the points where animations are most likely to land.
    """
    if not beats_data:
        return ""
    beats = beats_data.get("beats") or []
    bpm = beats_data.get("bpm") or 0.0
    if not beats:
        return ""

    interesting: list[float] = []
    for c in chapters or []:
        start = float(c.get("start") or 0.0)
        end = float(c.get("end") or 0.0)
        interesting.append(start)
        if end > start:
            interesting.append(end)
    for b in soundbites or []:
        interesting.append(float(b.get("start") or 0.0))

    if interesting:
        picked: list[float] = []
        used: set[int] = set()
        for t in interesting:
            # nearest 2 beats to each interesting moment
            ranked = sorted(range(len(beats)), key=lambda i: abs(beats[i] - t))
            for idx in ranked[:2]:
                if idx not in used:
                    picked.append(beats[idx])
                    used.add(idx)
                if len(picked) >= max_beats:
                    break
            if len(picked) >= max_beats:
                break
        if len(picked) < max_beats:
            # top up with evenly-spaced beats from the rest
            step = max(1, len(beats) // (max_beats - len(picked) + 1))
            for i in range(0, len(beats), step):
                if i in used:
                    continue
                picked.append(beats[i])
                used.add(i)
                if len(picked) >= max_beats:
                    break
        picked.sort()
    else:
        step = max(1, len(beats) // max_beats)
        picked = beats[::step][:max_beats]

    beat_list = ", ".join(f"{t:.2f}" for t in picked)
    return (
        f"<beats bpm=\"{bpm:.1f}\">\n"
        f"Strong beats (s): {beat_list}\n"
        f"</beats>"
    )
