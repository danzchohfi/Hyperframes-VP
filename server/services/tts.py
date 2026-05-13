"""OpenAI TTS wrapper for hook narration.

Uses the `tts-1` model (low latency, ~1-2s per request). Each synthesis
is content-hashed so re-rendering with the same (text, voice) is free.

The text comes from the reel animation's `tts` field (typically the
hook_card line); the voice from `tts_voice` (default "alloy"). Output
MP3 lives in `project_dir/.tts/{hash}.mp3` so it ships with the
composition and can be cached across renders.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

# OpenAI tts-1 voices (as of late 2024). Stable set.
VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
DEFAULT_VOICE = "alloy"
DEFAULT_MODEL = "tts-1"
DEFAULT_SPEED = 1.0


def _hash_input(text: str, voice: str, speed: float) -> str:
    payload = f"{voice}|{speed:.2f}|{text}".encode()
    return hashlib.sha1(payload).hexdigest()[:16]


def cache_path(project_dir: Path, text: str, voice: str, speed: float = DEFAULT_SPEED) -> Path:
    h = _hash_input(text, voice, speed)
    return project_dir / ".tts" / f"{h}.mp3"


async def synthesize(
    text: str,
    *,
    project_dir: Path,
    voice: str = DEFAULT_VOICE,
    speed: float = DEFAULT_SPEED,
) -> Path:
    """Render `text` to an MP3 in the project's .tts/ cache. Reuses an
    existing file when the content hash matches."""
    text = (text or "").strip()
    if not text:
        raise ValueError("tts: empty text")
    if voice not in VOICES:
        voice = DEFAULT_VOICE

    out = cache_path(project_dir, text, voice, speed)
    if out.exists() and out.stat().st_size > 0:
        return out

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing — TTS unavailable")

    out.parent.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(api_key=api_key)
    # SDK v1.57.x: prefer streaming_response context manager.
    async with client.audio.speech.with_streaming_response.create(
        model=DEFAULT_MODEL,
        voice=voice,
        input=text,
        speed=speed,
        response_format="mp3",
    ) as response:
        await response.stream_to_file(out)
    log.info("tts: synthesized %s (%d chars, voice=%s)", out.name, len(text), voice)
    return out


async def synthesize_for_animations(
    animations: list[dict], project_dir: Path,
) -> dict[str, Path]:
    """For each animation that carries a `tts` text, ensure the MP3 is
    cached. Returns a map of animation-key → Path so the renderer can
    drop the file into its filter graph or composition dir.

    Failures are logged and skipped (we don't want a single missing TTS
    to torch the whole render).
    """
    out: dict[str, Path] = {}
    for i, a in enumerate(animations or []):
        text = (a.get("tts") or "").strip()
        if not text:
            continue
        voice = a.get("tts_voice") or DEFAULT_VOICE
        speed = float(a.get("tts_speed") or DEFAULT_SPEED)
        try:
            p = await synthesize(text, project_dir=project_dir, voice=voice, speed=speed)
            out[f"anim_{i}"] = p
        except Exception:
            log.exception("tts for animation %d failed", i)
    return out
