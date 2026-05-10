"""OpenAI Whisper transcription with word-level timestamps."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI


def _client() -> AsyncOpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=key)


async def transcribe(audio_path: Path, *, language: str | None = None) -> dict[str, Any]:
    """Returns {text, language, duration, words: [{word, start, end}], segments: [...]}."""
    client = _client()
    with audio_path.open("rb") as f:
        resp = await client.audio.transcriptions.create(
            file=f,
            model="whisper-1",
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
            language=language,
        )

    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

    words = []
    for w in data.get("words", []) or []:
        words.append(
            {
                "word": w.get("word") or w.get("text") or "",
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0)),
            }
        )
    segments = []
    for s in data.get("segments", []) or []:
        segments.append(
            {
                "id": s.get("id"),
                "start": float(s.get("start", 0.0)),
                "end": float(s.get("end", 0.0)),
                "text": s.get("text", "").strip(),
            }
        )

    return {
        "text": data.get("text", ""),
        "language": data.get("language"),
        "duration": float(data.get("duration", 0.0) or 0.0),
        "words": words,
        "segments": segments,
    }
