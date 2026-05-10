"""LLM-driven topic-shift detection — produces chapters with timestamps
suitable for podcast YouTube descriptions and Hyperframes chapter cards.

Unlike `story.propose`, this doesn't reorder or pick — it walks the transcript
linearly and only marks where the topic shifts. Output works for full-length
podcasts where you want non-destructive chapter markers.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class Chapter(BaseModel):
    id: str
    name: str
    start: float
    end: float
    summary: str = ""


class ChaptersResult(BaseModel):
    chapters: list[Chapter] = Field(default_factory=list)


_SYSTEM = """You are an editor adding YouTube-style chapter markers to a long
interview/podcast.

You receive a numbered list of transcript segments. Mark where each chapter
starts — moments where the topic genuinely shifts, not every pause. Aim for
4-12 chapters across the whole recording.

Reply with ONLY a JSON object:

{
  "chapters": [
    { "name": "Intro", "start_segment": 0, "summary": "..." },
    { "name": "Origin story", "start_segment": 14, "summary": "..." },
    ...
  ]
}

`start_segment` is the index of the segment where the chapter begins."""


async def detect(transcript: dict[str, Any], *, max_chars: int = 14000) -> ChaptersResult:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    segments = transcript.get("segments") or []
    if not segments:
        return ChaptersResult()

    norm = [
        {"i": i, "start": float(s.get("start") or 0.0), "end": float(s.get("end") or 0.0),
         "text": (s.get("text") or "").strip()}
        for i, s in enumerate(segments)
    ]
    text = "\n".join(f"[{s['i']}] {s['start']:.1f}s: {s['text']}" for s in norm)
    if len(text) > max_chars:
        # Down-sample: keep every k-th segment
        k = max(1, len(text) // max_chars + 1)
        norm_sub = norm[::k]
        text = "\n".join(f"[{s['i']}] {s['start']:.1f}s: {s['text']}" for s in norm_sub)

    total_sec = norm[-1]["end"]

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Total duration: {total_sec:.0f}s\nSegments:\n\n{text}"},
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")

    seg_by_idx = {s["i"]: s for s in norm}
    raw: list[dict[str, Any]] = data.get("chapters") or []
    chapters: list[Chapter] = []
    # ensure sorted by start_segment
    raw.sort(key=lambda c: int(c.get("start_segment") or 0))
    for i, ch in enumerate(raw):
        idx = int(ch.get("start_segment") or 0)
        seg = seg_by_idx.get(idx)
        if not seg:
            continue
        start = seg["start"]
        # end = next chapter start, or total_sec
        if i + 1 < len(raw):
            next_idx = int(raw[i + 1].get("start_segment") or 0)
            next_seg = seg_by_idx.get(next_idx)
            end = next_seg["start"] if next_seg else total_sec
        else:
            end = total_sec
        chapters.append(Chapter(
            id=f"c{i + 1}",
            name=str(ch.get("name") or f"Chapter {i + 1}").strip(),
            start=start,
            end=end,
            summary=str(ch.get("summary") or "").strip(),
        ))

    return ChaptersResult(chapters=chapters)


def to_youtube_markdown(chapters: list[Chapter]) -> str:
    """Format chapters as 'mm:ss Name' lines suitable for a YouTube description."""
    lines = []
    for c in chapters:
        m = int(c.start) // 60
        s = int(c.start) % 60
        lines.append(f"{m:02d}:{s:02d} {c.name}")
    return "\n".join(lines)
