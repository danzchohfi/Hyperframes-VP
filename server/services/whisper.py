"""OpenAI Whisper transcription with word-level timestamps.

The Whisper API caps inbound payloads at 25 MB. A raw podcast MP4 (or
even a normalized H.264 MP4) blows past that within minutes, so this
module always re-encodes the input to a compact MP3 first
(mono, 16 kHz, 32 kbps — Whisper-optimal). A 90-minute podcast in this
format is ~21 MB, which fits in one request.

When the compact MP3 is still larger than the API limit (very long
recordings, > ~1h45), `transcribe` chunks the audio at fixed intervals,
transcribes each chunk concurrently, and merges the word + segment
lists with per-chunk time offsets.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from . import ffmpeg as ff

log = logging.getLogger(__name__)


def _cache_dir() -> Path:
    """~/.cache/hfvp/whisper — re-uses Whisper output across re-runs of
    the same source file. Whisper is the priciest step in the pipeline
    ($/minute), so cache misses only happen on truly new content."""
    root = Path(os.environ.get("HFVP_CACHE_DIR") or (Path.home() / ".cache" / "hfvp"))
    d = root / "whisper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
        log.info("whisper: cache wrote %s", path.name)
    except Exception as e:
        log.warning("whisper: cache write failed for %s: %s", path, e)

# OpenAI rejects > 26214400 bytes. Keep a margin so MP3 frame-tail rounding
# can't push us over.
MAX_BYTES = 24 * 1024 * 1024
CHUNK_SECONDS = 15 * 60   # 15 min @ 32 kbps mono = ~3.6 MB, very safe.

# libmp3lame prepends ~576-1152 audio samples (∼36-72 ms at 16 kHz) of
# silence as encoder padding. Whisper sees that silence and shifts every
# returned timestamp by that amount. Shave it off here so captions land on
# the actual word rather than ~50 ms after it.
LAME_PADDING_OFFSET = 0.050


def _client() -> AsyncOpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=key)


async def _to_compact_audio(src: Path, dst: Path) -> None:
    """Re-encode any input (video/audio) to MP3 mono 16 kHz 32 kbps."""
    await ff.run([
        "ffmpeg", "-y",
        "-i", str(src),
        "-vn",              # drop video
        "-ac", "1",         # mono
        "-ar", "16000",     # 16 kHz (Whisper-native)
        "-b:a", "32k",      # speech-quality MP3
        "-c:a", "libmp3lame",
        str(dst),
    ])


async def _split_audio(src: Path, dst_dir: Path, chunk_seconds: int = CHUNK_SECONDS) -> list[tuple[Path, float]]:
    """Cut `src` into roughly `chunk_seconds`-second pieces. Returns
    [(path, offset_seconds), ...]. Stream-copy so it's instant and the
    chunks stay the same bitrate."""
    template = dst_dir / "chunk_%03d.mp3"
    await ff.run([
        "ffmpeg", "-y",
        "-i", str(src),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-c:a", "copy",
        "-reset_timestamps", "1",
        str(template),
    ])
    # `-f segment` cuts on frame boundaries, so each chunk is within ~0.1s
    # of the requested length but never exact. Using i*chunk_seconds as the
    # offset accumulates drift in long podcasts (1h45+) — we'd see captions
    # land hundreds of ms late by the end. Build offsets from the actual
    # durations instead.
    paths = sorted(dst_dir.glob("chunk_*.mp3"))
    out: list[tuple[Path, float]] = []
    running = 0.0
    for p in paths:
        out.append((p, running))
        try:
            running += await ff.duration(p)
        except Exception:
            running += float(chunk_seconds)
    return out


async def _whisper_call(audio: Path, language: str | None) -> dict[str, Any]:
    client = _client()
    with audio.open("rb") as f:
        resp = await client.audio.transcriptions.create(
            file=f,
            model="whisper-1",
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
            language=language,
        )
    return resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)


def _normalize_payload(data: dict[str, Any], offset: float = 0.0) -> dict[str, Any]:
    # offset is "where this chunk starts in the parent file". We additionally
    # shift everything by -LAME_PADDING_OFFSET so caption timing matches the
    # original audio, not the silence-padded MP3 Whisper actually saw.
    shift = offset - LAME_PADDING_OFFSET

    def _t(v: Any) -> float:
        return max(0.0, float(v or 0.0) + shift)

    words: list[dict[str, Any]] = []
    for w in data.get("words", []) or []:
        words.append({
            "word": w.get("word") or w.get("text") or "",
            "start": _t(w.get("start")),
            "end": _t(w.get("end")),
        })
    segments: list[dict[str, Any]] = []
    for s in data.get("segments", []) or []:
        segments.append({
            "id": s.get("id"),
            "start": _t(s.get("start")),
            "end": _t(s.get("end")),
            "text": (s.get("text") or "").strip(),
        })
    return {
        "text": data.get("text", ""),
        "language": data.get("language"),
        "duration": float(data.get("duration", 0.0) or 0.0),
        "words": words,
        "segments": segments,
    }


async def transcribe(audio_path: Path, *, language: str | None = None) -> dict[str, Any]:
    """Public entry point — handles compact-encode + chunking automatically.

    Returns {text, language, duration, words, segments}.

    Results are cached by sha1(source_file) + language under
    ~/.cache/hfvp/whisper/<sha1>.<lang|auto>.json. Re-running on the
    same source.mp4 (common when re-rendering or tweaking downstream
    steps) returns instantly from disk.
    """
    cache_key = f"{_hash_file(audio_path)}.{language or 'auto'}.json"
    cache_path = _cache_dir() / cache_key
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
            log.info("whisper: cache hit %s", cache_path.name)
            return data
        except Exception as e:
            log.warning("whisper: ignoring corrupt cache %s: %s", cache_path, e)

    workdir = Path(tempfile.mkdtemp(prefix="hfvp_whisper_"))
    try:
        compact = workdir / "audio.mp3"
        await _to_compact_audio(audio_path, compact)
        size = compact.stat().st_size
        log.info("whisper: compact audio is %.1f MB", size / (1024 * 1024))

        if size <= MAX_BYTES:
            data = await _whisper_call(compact, language)
            result = _normalize_payload(data)
            _write_cache(cache_path, result)
            return result

        chunks_dir = workdir / "chunks"
        chunks_dir.mkdir()
        chunks = await _split_audio(compact, chunks_dir)
        log.info("whisper: chunking into %d pieces of ~%ds", len(chunks), CHUNK_SECONDS)
        if not chunks:
            raise RuntimeError("audio chunking produced no segments")

        # Run chunks concurrently with a small cap so we don't slam OpenAI.
        sem = asyncio.Semaphore(3)
        async def _one(p_off: tuple[Path, float]) -> dict[str, Any]:
            p, off = p_off
            async with sem:
                raw = await _whisper_call(p, language)
            return _normalize_payload(raw, offset=off)
        parts = await asyncio.gather(*[_one(c) for c in chunks])

        text = " ".join(p["text"].strip() for p in parts if p.get("text")).strip()
        words: list[dict[str, Any]] = []
        segments: list[dict[str, Any]] = []
        total_dur = 0.0
        for p in parts:
            words.extend(p["words"])
            segments.extend(p["segments"])
            if p.get("duration"):
                total_dur = max(total_dur, p["duration"])
        words.sort(key=lambda w: w["start"])
        segments.sort(key=lambda s: s["start"])
        for i, s in enumerate(segments):
            s["id"] = i
        result = {
            "text": text,
            "language": parts[0].get("language") if parts else None,
            "duration": total_dur if total_dur > 0 else float(CHUNK_SECONDS * len(parts)),
            "words": words,
            "segments": segments,
        }
        _write_cache(cache_path, result)
        return result
    finally:
        try:
            for p in workdir.rglob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
            for p in sorted(workdir.rglob("*"), reverse=True):
                if p.is_dir():
                    p.rmdir()
            workdir.rmdir()
        except Exception:
            pass
