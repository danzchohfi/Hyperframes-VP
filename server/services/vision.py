"""Computer-vision tagging for B-roll clips.

Samples N frames from a clip via ffmpeg, sends them to GPT-4o vision, and
returns a description + tags + categories that we use later to match clips
against A-roll soundbite topics.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .ffmpeg import run, duration as ffduration


class ClipTags(BaseModel):
    description: str = ""
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    quality: str = "ok"  # ok | shaky | blurry | dark
    has_people: bool = False


_SYSTEM = """You annotate raw B-roll footage for an editor.

Given 3-6 sample frames from a single clip, return a JSON object with:
- description: 1-2 sentences describing what's happening
- summary: 5-9 words, used as a clip title
- tags: 6-12 short tags (single words or 2-word phrases)
- categories: 1-3 broad categories (e.g. "interior", "exterior", "people",
  "product", "landscape", "city", "nature", "studio", "office", "transport")
- quality: "ok" if usable; "shaky", "blurry", or "dark" if there's a problem
- has_people: true if a person is clearly visible

Reply with ONLY the JSON object."""


async def _sample_frames(src: Path, n: int = 4) -> list[Path]:
    dur = await ffduration(src)
    if dur <= 0:
        raise RuntimeError("zero-duration source")
    n = max(1, min(n, 8))
    timestamps = [(dur * (i + 1)) / (n + 1) for i in range(n)]
    out_dir = Path(tempfile.mkdtemp(prefix="hf-vision-"))
    paths: list[Path] = []
    for i, t in enumerate(timestamps):
        out = out_dir / f"frame_{i:02d}.jpg"
        await run([
            "ffmpeg", "-y",
            "-ss", f"{t:.3f}",
            "-i", str(src),
            "-frames:v", "1",
            "-q:v", "3",
            "-vf", "scale=720:-2",
            str(out),
        ])
        if out.exists():
            paths.append(out)
    return paths


def _encode(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


async def tag_clip(src: Path, *, frames: int = 4) -> ClipTags:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    sampled = await _sample_frames(src, n=frames)
    if not sampled:
        raise RuntimeError("could not sample frames")

    try:
        content = [{"type": "text", "text": "Sample frames in chronological order."}]
        for p in sampled:
            content.append({"type": "image_url", "image_url": {"url": _encode(p), "detail": "low"}})

        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": content},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    finally:
        shutil.rmtree(sampled[0].parent, ignore_errors=True)

    return ClipTags(
        description=str(data.get("description") or "").strip(),
        summary=str(data.get("summary") or "").strip(),
        tags=[str(t) for t in (data.get("tags") or [])][:14],
        categories=[str(c) for c in (data.get("categories") or [])][:4],
        quality=str(data.get("quality") or "ok").lower(),
        has_people=bool(data.get("has_people")),
    )
