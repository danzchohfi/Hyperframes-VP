"""Multicam audio sync via cross-correlation.

Hybrid strategy:

1. Decode each clip's audio to 16 kHz mono.
2. Apply a speech-band bandpass (300–3400 Hz) to reject AC hum (60 Hz),
   table rumble, and HVAC noise that otherwise dominate the correlation
   peak in quiet rooms.
3. Compute the full-waveform cross-correlation (FFT-based). Use the
   first ~90 s of each clip — long enough to find offsets in long-form
   podcasts, short enough to be fast.
4. Parabolic interpolation around the peak gives sub-sample (≪1 ms)
   accuracy.
5. If the waveform correlation looks weak (score < 0.20), try an RMS
   envelope correlation as a fallback — it's less precise but more
   robust when mics have very different EQ / there's strong reverb.

Returns `{name, offset, score, reliable}`. `offset` is in seconds and
positive means the clip starts LATER than the reference.
"""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, correlate, sosfiltfilt

from .ffmpeg import run


SR_DEFAULT = 16000
WINDOW_SECONDS = 90        # max correlation window
ENV_HOP_MS = 20            # envelope sample every 20 ms (fallback only)
SPEECH_LOW_HZ = 300
SPEECH_HIGH_HZ = 3400
MIN_RELIABLE_SCORE = 0.20  # below this we still return the offset but flag it


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
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        try:
            out.unlink()
            out.parent.rmdir()
        except Exception:
            pass


def _bandpass(x: np.ndarray, sr: int, low: int, high: int) -> np.ndarray:
    """Speech-band Butterworth (4th order, zero-phase via sosfiltfilt)."""
    nyq = sr * 0.5
    lo = max(20.0, min(low, nyq - 100.0)) / nyq
    hi = min(nyq - 50.0, max(high, low + 200.0)) / nyq
    if hi <= lo:
        return x
    sos = butter(4, [lo, hi], btype="band", output="sos")
    try:
        return sosfiltfilt(sos, x).astype(np.float32)
    except Exception:
        return x


def _envelope(x: np.ndarray, *, sr: int, hop_ms: int = ENV_HOP_MS) -> np.ndarray:
    """Short-time RMS envelope (no slow-mean subtraction — that erased
    the speech bursts on low-SNR clips during testing)."""
    hop = max(1, int(sr * hop_ms / 1000.0))
    if x.size == 0:
        return np.zeros(0, dtype=np.float32)
    n = x.size // hop
    if n == 0:
        return np.array([float(np.sqrt(np.mean(x * x) + 1e-12))], dtype=np.float32)
    trimmed = x[: n * hop].reshape(n, hop)
    return np.sqrt(np.mean(trimmed * trimmed, axis=1) + 1e-12).astype(np.float32)


def _refine_peak(corr: np.ndarray, idx: int) -> float:
    """Parabolic interpolation around an integer peak. Returns the
    fractional shift in samples relative to `idx`."""
    if idx <= 0 or idx >= len(corr) - 1:
        return 0.0
    a, b, c = float(corr[idx - 1]), float(corr[idx]), float(corr[idx + 1])
    denom = (a - 2 * b + c)
    if abs(denom) < 1e-12:
        return 0.0
    return 0.5 * (a - c) / denom


def _normxcorr(a: np.ndarray, b: np.ndarray) -> tuple[int, float, np.ndarray]:
    """Returns (peak index, peak magnitude in [0,1], full correlation).
    Both inputs are mean-removed + L2-normalized before correlation
    so the peak amplitude is bounded in [-1, 1]."""
    a = a - a.mean()
    b = b - b.mean()
    a = a / (np.linalg.norm(a) + 1e-9)
    b = b / (np.linalg.norm(b) + 1e-9)
    corr = correlate(a, b, mode="full", method="fft")
    abs_corr = np.abs(corr)
    idx = int(np.argmax(abs_corr))
    peak = float(abs_corr[idx])
    return idx, min(1.0, peak), abs_corr


def _best_offset(
    ref: np.ndarray,
    other: np.ndarray,
    *,
    sr: int = SR_DEFAULT,
    window_seconds: int = WINDOW_SECONDS,
) -> tuple[float, float]:
    """Returns (offset_seconds, normalized_score in [0,1]).
    Positive offset → `other` starts LATER than the reference.
    """
    cap = sr * window_seconds
    a = ref[:cap]
    b = other[:cap]
    if a.size < sr // 4 or b.size < sr // 4:
        return 0.0, 0.0

    # Speech-band filter — reject hum + rumble that bias the peak.
    a_bp = _bandpass(a, sr, SPEECH_LOW_HZ, SPEECH_HIGH_HZ)
    b_bp = _bandpass(b, sr, SPEECH_LOW_HZ, SPEECH_HIGH_HZ)

    # Primary: full-waveform correlation. Works great when both mics
    # see roughly the same audio (typical multicam).
    idx, score_wf, corr = _normxcorr(a_bp, b_bp)
    frac = _refine_peak(corr, idx)
    lag_zero = len(b_bp) - 1
    lag_samples = (idx + frac) - lag_zero
    offset_sec = -float(lag_samples) / sr

    if score_wf >= MIN_RELIABLE_SCORE:
        return float(offset_sec), float(score_wf)

    # Fallback: envelope correlation. Phase-insensitive — works when
    # the two mics have very different frequency response or there's
    # strong reverb. Coarser but better than nothing.
    env_a = _envelope(a_bp, sr=sr)
    env_b = _envelope(b_bp, sr=sr)
    if env_a.size == 0 or env_b.size == 0:
        return float(offset_sec), float(score_wf)
    idx_e, score_e, corr_e = _normxcorr(env_a, env_b)
    frac_e = _refine_peak(corr_e, idx_e)
    lag_zero_e = len(env_b) - 1
    lag_env_samples = (idx_e + frac_e) - lag_zero_e
    offset_env_sec = -(lag_env_samples * ENV_HOP_MS / 1000.0)

    # Pick whichever has the higher confidence.
    if score_e > score_wf:
        return float(offset_env_sec), float(score_e)
    return float(offset_sec), float(score_wf)


async def compute_offsets(
    clips: list[tuple[str, Path]],
    *,
    sr: int = SR_DEFAULT,
    window_seconds: int = WINDOW_SECONDS,
) -> list[dict]:
    """Align every clip to clip 0.

    Returns a list of dicts with name, offset (s), score [0,1], reliable
    (bool). The reference itself gets offset=0.0, score=1.0,
    reliable=True.
    """
    if not clips:
        return []
    name0, path0 = clips[0]
    try:
        ref = await _decode(path0, sr=sr)
    except Exception as e:
        return [{"name": name0, "offset": 0.0, "score": 0.0, "reliable": False, "error": str(e)}]

    if ref.size == 0:
        return [{"name": name0, "offset": 0.0, "score": 0.0, "reliable": False}]

    out: list[dict] = [{"name": name0, "offset": 0.0, "score": 1.0, "reliable": True}]
    for name, path in clips[1:]:
        try:
            other = await _decode(path, sr=sr)
            off, score = _best_offset(ref, other, sr=sr, window_seconds=window_seconds)
            out.append({
                "name": name,
                "offset": float(round(off, 3)),
                "score": float(round(score, 4)),
                "reliable": bool(score >= MIN_RELIABLE_SCORE),
            })
        except Exception as e:
            out.append({
                "name": name, "offset": 0.0, "score": 0.0,
                "reliable": False, "error": str(e),
            })
    return out
