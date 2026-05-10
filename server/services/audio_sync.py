"""Multicam audio sync via cross-correlation.

Decodes each clip's audio to 8 kHz mono, computes FFT-based cross-correlation
against a reference, and returns the best-fit offset in seconds. Use the
offset to align clips in FCPXML multicam.

This works well when the clips share a common audio source (e.g., two cameras
recording the same room) and won't sync clips that have disjoint audio.
"""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.signal import correlate

from .ffmpeg import run


SR_DEFAULT = 8000


async def _decode(src: Path, *, sr: int = SR_DEFAULT) -> np.ndarray:
    out = Path(tempfile.mkdtemp(prefix="hf-sync-")) / "audio.wav"
    try:
        await run([
            "ffmpeg", "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", str(sr),
            "-acodec", "pcm_s16le", str(out),
        ])
        with wave.open(str(out), "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            sr_real = wf.getframerate()
        if sr_real != sr:
            # let it through; only used for proportional offset calc
            pass
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        try:
            out.unlink()
            out.parent.rmdir()
        except Exception:
            pass


def _best_offset(ref: np.ndarray, other: np.ndarray, *, sr: int = SR_DEFAULT) -> tuple[float, float]:
    """Returns (offset_seconds, normalized_score). Positive offset means
    `other` lags the reference (i.e., starts later).

    Truncates both signals to first ~30 s to keep correlation fast.
    """
    cap = sr * 30
    a = ref[:cap]
    b = other[:cap]
    if len(a) == 0 or len(b) == 0:
        return 0.0, 0.0
    # Normalize energy
    a = a - a.mean()
    b = b - b.mean()
    a /= (np.linalg.norm(a) + 1e-9)
    b /= (np.linalg.norm(b) + 1e-9)
    # Full cross-correlation via FFT
    corr = correlate(a, b, mode="full", method="fft")
    lag_zero = len(b) - 1
    best = int(np.argmax(np.abs(corr)))
    lag_samples = best - lag_zero
    # Convention: positive offset means `other` STARTS LATER than `ref`.
    # correlate(ref, other) peaks at -delay when other is a delayed copy,
    # so we negate.
    offset_sec = -lag_samples / sr
    score = float(abs(corr[best]))
    return offset_sec, score


async def compute_offsets(
    clips: list[tuple[str, Path]],
    *,
    sr: int = SR_DEFAULT,
) -> list[dict]:
    """Each clip becomes a {name, offset, score} relative to clip 0. The
    reference clip has offset 0.0 and score 1.0.
    """
    if not clips:
        return []
    name0, path0 = clips[0]
    ref = await _decode(path0, sr=sr)
    if ref.size == 0:
        return [{"name": name0, "offset": 0.0, "score": 0.0}]
    a0 = ref / (np.linalg.norm(ref - ref.mean()) + 1e-9)
    self_norm = float(np.abs(correlate(a0, a0, mode="full", method="fft")).max())
    out: list[dict] = [{"name": name0, "offset": 0.0, "score": 1.0}]
    for name, path in clips[1:]:
        try:
            other = await _decode(path, sr=sr)
            off, score = _best_offset(ref, other, sr=sr)
            out.append({
                "name": name,
                "offset": float(round(off, 3)),
                "score": float(round(score / self_norm, 4)) if self_norm > 0 else 0.0,
            })
        except Exception as e:
            out.append({"name": name, "offset": 0.0, "score": 0.0, "error": str(e)})
    return out
