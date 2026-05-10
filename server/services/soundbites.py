"""LLM-driven soundbite extraction with topic grouping.

Given the Whisper transcript, asks the model to:
  1. Identify the strongest soundbites (clear, emotional, quotable lines)
  2. Group them into 3-6 topics covering the main themes
  3. Score each soundbite on quotability (0-100)

Times are derived from the transcript word/segment timings — the LLM only
references segment indices, never invents timestamps.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class Soundbite(BaseModel):
    id: str
    start: float
    end: float
    text: str
    topic: str
    score: int = 50
    summary: str = ""


class Topic(BaseModel):
    id: str
    name: str
    summary: str = ""
    soundbite_ids: list[str] = Field(default_factory=list)


class SoundbiteAnalysis(BaseModel):
    language: str | None = None
    soundbites: list[Soundbite] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)


_SYSTEM = """You are a senior video editor reviewing an interview transcript.

Your job:
1. Identify the strongest soundbites — lines that are clear, emotionally
   resonant, quotable, or load-bearing for the story. Skip filler, repetition,
   throat-clearing, and weak phrasing.
2. Group soundbites into 3-6 topics that cover the main themes.
3. Score each soundbite 0-100 on quotability + story value.

You must reference segments by their numeric index. Never invent timestamps.

Reply with ONLY a JSON object of this shape:

{
  "soundbites": [
    {
      "id": "sb1",
      "segment_ids": [3, 4, 5],   // consecutive or near-consecutive segments forming one statement
      "topic_id": "t1",
      "score": 85,                 // 0-100
      "summary": "Single short line describing the bite"
    }
  ],
  "topics": [
    { "id": "t1", "name": "Origin story", "summary": "How the speaker started" }
  ]
}

Pick only the top ~30% of segments. Order soundbites by score descending."""


def _build_segments_text(segments: list[dict], words: list[dict]) -> tuple[list[dict], str]:
    """Return normalized segments + a numbered string for the LLM prompt."""
    if segments:
        norm = [
            {
                "i": i,
                "start": float(s.get("start") or 0.0),
                "end": float(s.get("end") or 0.0),
                "text": (s.get("text") or "").strip(),
            }
            for i, s in enumerate(segments)
        ]
    else:
        # Fallback: bucket words into ~5s segments
        norm = []
        bucket: list[dict] = []
        bucket_start = None
        for w in words:
            ws = float(w["start"])
            if bucket_start is None:
                bucket_start = ws
            bucket.append(w)
            if ws - bucket_start > 5.0:
                norm.append({
                    "i": len(norm),
                    "start": bucket_start,
                    "end": float(bucket[-1]["end"]),
                    "text": " ".join(b["word"] for b in bucket).strip(),
                })
                bucket = []
                bucket_start = None
        if bucket:
            norm.append({
                "i": len(norm),
                "start": bucket_start or 0.0,
                "end": float(bucket[-1]["end"]),
                "text": " ".join(b["word"] for b in bucket).strip(),
            })

    text = "\n".join(
        f"[{s['i']}] {s['start']:.2f}-{s['end']:.2f}: {s['text']}"
        for s in norm
    )
    return norm, text


async def analyze(transcript: dict, *, max_chars: int = 18000) -> SoundbiteAnalysis:
    """Run the LLM analysis and assemble the SoundbiteAnalysis."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    segments = transcript.get("segments") or []
    words = transcript.get("words") or []
    norm_segments, segments_text = _build_segments_text(segments, words)

    if len(segments_text) > max_chars:
        segments_text = segments_text[:max_chars] + "\n[... transcript truncated ...]"

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Transcript ({len(norm_segments)} segments):\n\n{segments_text}"},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    data: dict[str, Any] = json.loads(raw)

    return _hydrate(data, norm_segments, language=transcript.get("language"))


def _hydrate(data: dict, segments: list[dict], *, language: str | None) -> SoundbiteAnalysis:
    seg_by_idx = {s["i"]: s for s in segments}

    topics_in = data.get("topics") or []
    topics: dict[str, Topic] = {}
    for t in topics_in:
        tid = str(t.get("id") or f"t{len(topics) + 1}")
        topics[tid] = Topic(
            id=tid,
            name=str(t.get("name") or "Topic").strip(),
            summary=str(t.get("summary") or "").strip(),
        )

    soundbites: list[Soundbite] = []
    for sb in data.get("soundbites") or []:
        seg_ids = sb.get("segment_ids") or []
        if not isinstance(seg_ids, list) or not seg_ids:
            continue
        starts: list[float] = []
        ends: list[float] = []
        texts: list[str] = []
        for idx in seg_ids:
            seg = seg_by_idx.get(int(idx))
            if not seg:
                continue
            starts.append(seg["start"])
            ends.append(seg["end"])
            texts.append(seg["text"])
        if not starts:
            continue
        topic_id = str(sb.get("topic_id") or "").strip()
        if topic_id and topic_id not in topics:
            topics[topic_id] = Topic(id=topic_id, name=topic_id, summary="")
        if not topic_id and topics:
            topic_id = next(iter(topics))
        sid = str(sb.get("id") or f"sb{len(soundbites) + 1}")
        bite = Soundbite(
            id=sid,
            start=min(starts),
            end=max(ends),
            text=" ".join(texts).strip(),
            topic=topic_id or "uncategorized",
            score=int(sb.get("score") or 50),
            summary=str(sb.get("summary") or "").strip(),
        )
        soundbites.append(bite)
        if topic_id in topics:
            topics[topic_id].soundbite_ids.append(sid)

    soundbites.sort(key=lambda b: -b.score)

    return SoundbiteAnalysis(
        language=language,
        soundbites=soundbites,
        topics=list(topics.values()),
    )
