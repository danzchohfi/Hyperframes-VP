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


def _best_offset_window(
    ref_slice: np.ndarray,
    other_slice: np.ndarray,
    *,
    sr: int,
) -> tuple[float, float]:
    """Same algorithm as `_best_offset` but on pre-sliced windows (the
    caller has already chosen which seconds to correlate). Used by the
    multi-window verification path."""
    if ref_slice.size < sr // 4 or other_slice.size < sr // 4:
        return 0.0, 0.0
    a_bp = _bandpass(ref_slice, sr, SPEECH_LOW_HZ, SPEECH_HIGH_HZ)
    b_bp = _bandpass(other_slice, sr, SPEECH_LOW_HZ, SPEECH_HIGH_HZ)
    idx, score_wf, corr = _normxcorr(a_bp, b_bp)
    frac = _refine_peak(corr, idx)
    lag_zero = len(b_bp) - 1
    lag_samples = (idx + frac) - lag_zero
    return -float(lag_samples) / sr, float(score_wf)


def _multi_window_sync(
    ref: np.ndarray,
    other: np.ndarray,
    *,
    sr: int,
    window_seconds: int = 30,
    n_windows: int = 3,
) -> dict:
    """Compute the offset between two clips using N independent windows.

    Strategy: take `n_windows` evenly spaced positions across the
    overlap of both clips (e.g. 10%, 50%, 90% of the shorter clip).
    Correlate each ref-window against the SAME absolute time-range in
    `other` (extended by ±window_seconds to allow large offsets to
    show up at the peak). If the resulting offsets agree across
    windows (std < AGREEMENT_THRESHOLD), we have high confidence and
    return their mean; if they disagree, we flag drift.

    Why this matters: long recordings where one camera's clock drifts
    relative to the master (cheap consumer cams do this) produce a
    correct sync at minute 0 and a 2-3 second skew by minute 30. The
    single-window correlation can't tell — it always reports SOME
    offset. Multi-window catches it.

    Returns:
        {offset, score, reliable, drift, windows: [(t_anchor_s, offset, score)...]}
    """
    AGREEMENT_THRESHOLD = 0.25  # seconds of std between windows
    DRIFT_FLAG_THRESHOLD = 0.5  # max-min across windows that triggers a drift warning
    min_len = min(ref.size, other.size)
    overlap_dur = min_len / sr
    if overlap_dur < window_seconds + 2:
        # Too short for multi-window — fall back to single-window.
        off, score = _best_offset(ref, other, sr=sr, window_seconds=window_seconds)
        return {
            "offset": float(off),
            "score": float(score),
            "reliable": score >= MIN_RELIABLE_SCORE,
            "drift": False,
            "windows": [(overlap_dur / 2.0, float(off), float(score))],
        }

    # Place anchor points across the recording, leaving a margin on each
    # side so we can extract a window around each anchor. Anchors are in
    # the COMMON time domain (relative to ref).
    margin = window_seconds // 2 + 1
    usable_start = margin
    usable_end = max(usable_start + 1, overlap_dur - margin)
    if n_windows <= 1:
        anchors = [(usable_start + usable_end) / 2.0]
    else:
        step = (usable_end - usable_start) / (n_windows - 1)
        anchors = [usable_start + i * step for i in range(n_windows)]

    half = window_seconds / 2.0
    window_results: list[tuple[float, float, float]] = []
    for t_anchor in anchors:
        ref_lo = int(max(0, (t_anchor - half) * sr))
        ref_hi = int(min(ref.size, (t_anchor + half) * sr))
        # Other gets a wider window so the cross-correlation can find a
        # lag even if `other` is shifted by up to ~half seconds.
        oth_lo = int(max(0, (t_anchor - half - half) * sr))
        oth_hi = int(min(other.size, (t_anchor + half + half) * sr))
        if ref_hi - ref_lo < sr // 4 or oth_hi - oth_lo < sr // 4:
            continue
        local_off, score = _best_offset_window(ref[ref_lo:ref_hi], other[oth_lo:oth_hi], sr=sr)
        # The local correlation gives the lag between the two slices in
        # their local timebase. Since we offset `other` window's start
        # by -half relative to ref's window start, the LOCAL lag needs
        # that compensation: absolute_offset = local_offset - half +
        # half = local_offset (the half cancels). But the windowed view
        # of `other` started `half` seconds earlier, so a perfect match
        # means the slices align when other_offset = +half. Adjust:
        #   true_offset_at_anchor = local_off + ((oth_lo - ref_lo + half*sr) / sr)
        # Simpler: just subtract the start-window-difference in seconds.
        window_offset_diff = (oth_lo - ref_lo) / sr
        absolute_offset = local_off + window_offset_diff
        window_results.append((t_anchor, absolute_offset, score))

    if not window_results:
        return {"offset": 0.0, "score": 0.0, "reliable": False, "drift": False, "windows": []}

    # Filter out very-low-score windows before averaging (they're noise).
    good = [r for r in window_results if r[2] >= MIN_RELIABLE_SCORE]
    used = good or window_results  # if none scored well, use them all (best effort)
    offsets = np.array([r[1] for r in used], dtype=np.float64)
    scores = np.array([r[2] for r in used], dtype=np.float64)

    # Median is robust to a single outlier window (one noisy segment).
    median_offset = float(np.median(offsets))
    # Inverse-variance-style: weight by score, but if all scores are low,
    # fall back to median.
    if scores.sum() > 0:
        weighted = float(np.average(offsets, weights=scores))
    else:
        weighted = median_offset
    spread = float(np.std(offsets))
    drift = (offsets.max() - offsets.min()) > DRIFT_FLAG_THRESHOLD

    # Confidence: high score from individual windows + low spread between
    # them. Capped so noisy-but-agreeing windows can still report reliable.
    mean_score = float(scores.mean())
    agreement_penalty = max(0.0, min(1.0, spread / AGREEMENT_THRESHOLD))
    composite_score = mean_score * (1.0 - 0.4 * agreement_penalty)

    return {
        "offset": float(round(weighted, 3)),
        "score": float(round(composite_score, 4)),
        "reliable": composite_score >= MIN_RELIABLE_SCORE and not drift,
        "drift": drift,
        "spread_s": float(round(spread, 3)),
        "windows": [(float(round(t, 2)), float(round(o, 3)), float(round(s, 4))) for t, o, s in window_results],
    }


async def compute_offsets(
    clips: list[tuple[str, Path]],
    *,
    sr: int = SR_DEFAULT,
    window_seconds: int = WINDOW_SECONDS,
) -> list[dict]:
    """Align every clip to clip 0.

    Uses the multi-window verification path: takes 3 evenly-spaced
    windows across the overlap of each (ref, other) pair, computes a
    local offset at each, and reports the weighted average + a drift
    flag when the windows disagree. Long recordings with clock drift
    show up as drift=true and reliable=false so the UI / FCPXML can
    nudge the editor to verify manually.

    Returns a list of dicts with: name, offset (s), score [0,1],
    reliable (bool), drift (bool, only for non-ref clips), spread_s
    (std of per-window offsets in seconds), windows (per-window
    detail). The reference itself gets offset=0.0, score=1.0,
    reliable=True, drift=False.
    """
    if not clips:
        return []
    name0, path0 = clips[0]
    try:
        ref = await _decode(path0, sr=sr)
    except Exception as e:
        return [{"name": name0, "offset": 0.0, "score": 0.0, "reliable": False, "drift": False, "error": str(e)}]

    if ref.size == 0:
        return [{"name": name0, "offset": 0.0, "score": 0.0, "reliable": False, "drift": False}]

    out: list[dict] = [{
        "name": name0, "offset": 0.0, "score": 1.0,
        "reliable": True, "drift": False, "windows": [],
    }]
    for name, path in clips[1:]:
        try:
            other = await _decode(path, sr=sr)
            res = _multi_window_sync(ref, other, sr=sr)
            out.append({
                "name": name,
                "offset": res["offset"],
                "score": res["score"],
                "reliable": res["reliable"],
                "drift": res["drift"],
                "spread_s": res.get("spread_s", 0.0),
                "windows": res["windows"],
            })
        except Exception as e:
            out.append({
                "name": name, "offset": 0.0, "score": 0.0,
                "reliable": False, "drift": False, "error": str(e),
            })
    return out
