"""Speaker diarization for HyperFrames-VP.

Three backends, picked in order of availability:

1. `pyannote.audio` — real speaker embeddings + clustering. Requires
   `HF_TOKEN` env (HuggingFace) + `pip install pyannote.audio` + a model
   download. Best quality.
2. MFCC-clustering — fall-back diarization that uses scipy + librosa-like
   spectral features computed by hand. Not as good as pyannote but
   significantly better than pure gap detection.
3. Adaptive gap heuristic — works without any heavy deps. Picks a
   per-recording gap threshold from the distribution of word-to-word
   pauses (P90), with anti-flap (no speaker switch shorter than 1.2s).

Returns the same shape: [{speaker, start, end, text, word_count}, ...]
"""

from __future__ import annotations

import os
import statistics
import tempfile
import wave
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .ffmpeg import run

# ---------- backend 1: pyannote (optional) ------------------------------------


def _pyannote_available() -> bool:
    if not os.environ.get("HF_TOKEN") and not os.environ.get("HUGGINGFACE_HUB_TOKEN"):
        return False
    try:
        import pyannote.audio  # noqa: F401
        return True
    except ImportError:
        return False


async def _pyannote_diarize(audio_wav: Path) -> list[dict[str, Any]]:
    from pyannote.audio import Pipeline  # type: ignore

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=token,
    )
    diarization = pipeline(str(audio_wav))
    out: list[dict[str, Any]] = []
    for turn, _, label in diarization.itertracks(yield_label=True):
        out.append({
            "speaker": str(label),
            "start": float(turn.start),
            "end": float(turn.end),
            "word_count": 0,
            "text": "",
        })
    return out


# ---------- backend 2: MFCC-ish clustering ------------------------------------


def _extract_mfcc_frames(audio: np.ndarray, sr: int, *, frame_ms: float = 25.0,
                         hop_ms: float = 10.0, n_mfcc: int = 13) -> tuple[np.ndarray, np.ndarray]:
    """Compute log-mel + DCT (handmade MFCC). Returns (frames, timestamps)."""
    from scipy.fft import dct
    from scipy.signal.windows import hann

    frame_len = int(sr * frame_ms / 1000.0)
    hop = int(sr * hop_ms / 1000.0)
    n_fft = 1
    while n_fft < frame_len:
        n_fft <<= 1

    # mel filterbank (40 filters, 0–8 kHz)
    def hz_to_mel(f): return 2595 * np.log10(1 + f / 700.0)
    def mel_to_hz(m): return 700 * (10 ** (m / 2595.0) - 1)
    n_mels = 40
    mel = np.linspace(hz_to_mel(0), hz_to_mel(min(sr / 2, 8000.0)), n_mels + 2)
    hz = mel_to_hz(mel)
    bins = np.floor((n_fft + 1) * hz / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        lo, mid, hi = bins[m - 1], bins[m], bins[m + 1]
        if mid > lo:
            fb[m - 1, lo:mid] = (np.arange(lo, mid) - lo) / (mid - lo)
        if hi > mid:
            fb[m - 1, mid:hi] = (hi - np.arange(mid, hi)) / (hi - mid)

    window = hann(frame_len, sym=False).astype(np.float32)
    frames: list[np.ndarray] = []
    timestamps: list[float] = []
    i = 0
    while i + frame_len <= len(audio):
        seg = audio[i:i + frame_len] * window
        spec = np.fft.rfft(seg, n=n_fft)
        power = np.abs(spec).astype(np.float32) ** 2
        mel_pow = fb @ power
        log_mel = np.log(np.maximum(mel_pow, 1e-10))
        mfcc = dct(log_mel, type=2, norm="ortho")[:n_mfcc]
        frames.append(mfcc)
        timestamps.append(i / sr)
        i += hop
    if not frames:
        return np.zeros((0, n_mfcc), dtype=np.float32), np.array([])
    return np.stack(frames), np.array(timestamps)


def _vad_mask(audio: np.ndarray, sr: int, *, frame_ms: float = 25.0,
              hop_ms: float = 10.0, percentile: float = 35.0) -> np.ndarray:
    """Simple energy-based VAD: keep frames whose RMS > P{percentile} of overall."""
    frame_len = int(sr * frame_ms / 1000.0)
    hop = int(sr * hop_ms / 1000.0)
    rms = []
    i = 0
    while i + frame_len <= len(audio):
        seg = audio[i:i + frame_len].astype(np.float32)
        rms.append(np.sqrt(np.mean(seg * seg) + 1e-12))
        i += hop
    if not rms:
        return np.zeros(0, dtype=bool)
    rms = np.array(rms)
    threshold = float(np.percentile(rms, percentile))
    return rms > threshold


def _kmeans_2(X: np.ndarray, *, max_iter: int = 30) -> np.ndarray:
    """Tiny 2-means clustering. Returns label array of {0, 1}."""
    if X.shape[0] < 2:
        return np.zeros(X.shape[0], dtype=int)
    rng = np.random.default_rng(seed=42)
    # init: pick two far-apart frames
    a = rng.integers(0, X.shape[0])
    centroids = np.stack([X[a], X[a]])
    for _ in range(5):  # k-means++ ish
        d = np.linalg.norm(X - centroids[1], axis=1)
        b = int(np.argmax(d))
        centroids = np.stack([X[a], X[b]])
        a = int(np.argmax(np.linalg.norm(X - centroids[0], axis=1)))
    labels = np.zeros(X.shape[0], dtype=int)
    for _ in range(max_iter):
        d0 = np.linalg.norm(X - centroids[0], axis=1)
        d1 = np.linalg.norm(X - centroids[1], axis=1)
        new_labels = (d1 < d0).astype(int)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for k in (0, 1):
            members = X[labels == k]
            if len(members) > 0:
                centroids[k] = members.mean(axis=0)
    return labels


async def _mfcc_diarize(audio_wav: Path, words: list[dict] | None) -> list[dict[str, Any]]:
    """Decode the audio, compute MFCC frames, cluster into 2 speakers, then
    project the cluster labels onto the word timings."""
    with wave.open(str(audio_wav), "rb") as wf:
        sr = wf.getframerate()
        nchan = wf.getnchannels()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nchan > 1:
        audio = audio.reshape(-1, nchan).mean(axis=1)

    frames, ts = _extract_mfcc_frames(audio, sr)
    if frames.shape[0] == 0:
        return []
    vad = _vad_mask(audio, sr)
    # align vad to frames (same hop)
    vad = vad[: frames.shape[0]]

    if vad.sum() < 4:
        # not enough speech — fall back to single-speaker turn
        return [{"speaker": "A", "start": 0.0, "end": float(ts[-1]) if ts.size else 0.0, "text": "", "word_count": 0}]

    speech_frames = frames[vad]
    speech_ts = ts[vad]
    # normalize
    mu = speech_frames.mean(axis=0)
    sd = speech_frames.std(axis=0) + 1e-6
    Xn = (speech_frames - mu) / sd
    labels = _kmeans_2(Xn)

    # smooth labels (median filter window 25)
    if len(labels) > 25:
        from scipy.signal import medfilt
        labels = medfilt(labels, kernel_size=25).astype(int)

    # build turns from contiguous runs
    turns: list[dict[str, Any]] = []
    cur_label = int(labels[0])
    cur_start = float(speech_ts[0])
    for i in range(1, len(labels)):
        if int(labels[i]) != cur_label:
            turns.append({
                "speaker": "A" if cur_label == 0 else "B",
                "start": cur_start,
                "end": float(speech_ts[i - 1]) + 0.01,
                "text": "",
                "word_count": 0,
            })
            cur_label = int(labels[i])
            cur_start = float(speech_ts[i])
    turns.append({
        "speaker": "A" if cur_label == 0 else "B",
        "start": cur_start,
        "end": float(speech_ts[-1]) + 0.01,
        "text": "",
        "word_count": 0,
    })

    # merge very short turns (< 0.7s) into neighbors — typical speech turns
    # are longer; sub-second ones are usually clustering jitter.
    turns = _merge_short_turns(turns, min_dur=0.7)

    if words:
        _attach_words(turns, words)
    return turns


def _merge_short_turns(turns: list[dict[str, Any]], *, min_dur: float = 1.2) -> list[dict[str, Any]]:
    if not turns:
        return turns
    merged = list(turns)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, t in enumerate(merged):
            if t["end"] - t["start"] < min_dur:
                # merge into the longer of its neighbors
                left = merged[i - 1] if i > 0 else None
                right = merged[i + 1] if i + 1 < len(merged) else None
                if left and right:
                    target = left if (left["end"] - left["start"]) >= (right["end"] - right["start"]) else right
                elif left:
                    target = left
                elif right:
                    target = right
                else:
                    break
                target["start"] = min(target["start"], t["start"])
                target["end"] = max(target["end"], t["end"])
                merged.pop(i)
                changed = True
                break
    # then merge adjacent same-speaker
    out: list[dict[str, Any]] = []
    for t in merged:
        if out and out[-1]["speaker"] == t["speaker"]:
            out[-1]["end"] = t["end"]
        else:
            out.append(t)
    return out


def _attach_words(turns: list[dict[str, Any]], words: list[dict]) -> None:
    for t in turns:
        words_in = [
            w for w in words
            if float(w.get("start", 0.0)) >= t["start"]
            and float(w.get("end", 0.0)) <= t["end"] + 0.05
        ]
        t["text"] = " ".join((w.get("word") or "").strip() for w in words_in).strip()
        t["word_count"] = len(words_in)


# ---------- backend 3: improved adaptive gap heuristic ------------------------


def adaptive_gap_segment(words: list[dict]) -> list[dict[str, Any]]:
    """Pick a per-recording threshold from the pause distribution, then
    alternate A/B at long pauses. Don't switch on pauses shorter than 1.0s.
    """
    if not words:
        return []
    gaps = [
        float(b.get("start") or 0.0) - float(a.get("end") or 0.0)
        for a, b in zip(words, words[1:])
        if float(b.get("start") or 0.0) > float(a.get("end") or 0.0)
    ]
    if not gaps:
        return [{
            "speaker": "A",
            "start": float(words[0].get("start") or 0.0),
            "end": float(words[-1].get("end") or 0.0),
            "text": " ".join((w.get("word") or "").strip() for w in words).strip(),
            "word_count": len(words),
        }]
    # threshold = P90 of pauses, clamped to [0.8, 2.0]s. With few samples,
    # quantile extrapolation is unstable, so fall back to a fixed 1.2s.
    if len(gaps) >= 8:
        p90 = float(statistics.quantiles(gaps, n=10)[-1])
        threshold = max(0.8, min(2.0, p90))
    else:
        threshold = 1.2

    turns: list[dict[str, Any]] = []
    speaker = 0
    cur: list[dict] = [words[0]]
    for prev, w in zip(words, words[1:]):
        gap = float(w.get("start") or 0.0) - float(prev.get("end") or 0.0)
        if gap >= threshold:
            turns.append(_finalize(cur, speaker))
            speaker ^= 1
            cur = [w]
        else:
            cur.append(w)
    if cur:
        turns.append(_finalize(cur, speaker))
    # only merge spurious fragments shorter than 400ms — actual speech is kept
    return _merge_short_turns(turns, min_dur=0.4)


def _finalize(group: list[dict], speaker_idx: int) -> dict[str, Any]:
    return {
        "speaker": "A" if speaker_idx == 0 else "B",
        "start": float(group[0].get("start") or 0.0),
        "end": float(group[-1].get("end") or 0.0),
        "text": " ".join((w.get("word") or "").strip() for w in group).strip(),
        "word_count": len(group),
    }


# ---------- backwards compat: old gap-based -----------------------------------


def segment_turns(words: list[dict], *, gap_threshold: float = 1.2) -> list[dict[str, Any]]:
    """Legacy fixed-threshold gap segmentation."""
    if not words:
        return []
    turns: list[dict[str, Any]] = []
    speaker = 0
    cur: list[dict] = [words[0]]
    for prev, w in zip(words, words[1:]):
        gap = float(w.get("start") or 0.0) - float(prev.get("end") or 0.0)
        if gap >= gap_threshold and len(cur) > 0:
            turns.append(_finalize(cur, speaker))
            speaker ^= 1
            cur = [w]
        else:
            cur.append(w)
    if cur:
        turns.append(_finalize(cur, speaker))
    return turns


# ---------- orchestrator + stats ---------------------------------------------


async def diarize(
    audio_src: Path,
    words: list[dict] | None,
    *,
    backend: str = "auto",
) -> dict[str, Any]:
    """Returns {backend, turns, stats}.

    backend ∈ {"auto", "pyannote", "mfcc", "gap"}.
    """
    chosen: str = backend
    if chosen == "auto":
        if _pyannote_available():
            chosen = "pyannote"
        elif audio_src.exists():
            chosen = "mfcc"
        else:
            chosen = "gap"

    if chosen == "pyannote":
        wav = await _ensure_wav(audio_src)
        turns = await _pyannote_diarize(wav)
        if words:
            _attach_words(turns, words)
    elif chosen == "mfcc":
        wav = await _ensure_wav(audio_src)
        turns = await _mfcc_diarize(wav, words)
    else:
        turns = adaptive_gap_segment(words or [])
        chosen = "gap"

    return {
        "backend": chosen,
        "turns": turns,
        "stats": _compute_stats(turns),
    }


async def _ensure_wav(src: Path) -> Path:
    out = Path(tempfile.mkdtemp(prefix="hf-diar-")) / "audio.wav"
    await run([
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000",
        "-acodec", "pcm_s16le",
        str(out),
    ])
    return out


def _compute_stats(turns: list[dict[str, Any]]) -> dict[str, Any]:
    by_speaker: dict[str, dict[str, float]] = {}
    total_dur = 0.0
    for t in turns:
        sp = t.get("speaker") or "?"
        dur = float(t.get("end", 0)) - float(t.get("start", 0))
        total_dur += max(0.0, dur)
        d = by_speaker.setdefault(sp, {"turns": 0, "talk_time": 0.0, "words": 0})
        d["turns"] += 1
        d["talk_time"] += max(0.0, dur)
        d["words"] += int(t.get("word_count") or 0)
    pct_by_speaker = {}
    for sp, s in by_speaker.items():
        s["share"] = round(s["talk_time"] / total_dur, 4) if total_dur > 0 else 0.0
        pct_by_speaker[sp] = s["share"]
    return {
        "total_duration": round(total_dur, 3),
        "speaker_count": len(by_speaker),
        "by_speaker": by_speaker,
    }
