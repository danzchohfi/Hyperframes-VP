"""Vlog mode: many short clips → LLM proposes narratives → assemble.

Workflow:
  1. User uploads N clips (each is normalized to clips/<id>.mp4).
  2. Each clip is transcribed individually.
  3. propose_narratives() asks the LLM to read all per-clip transcripts and
     return 3-5 different ways the clips could be assembled into a coherent
     story. Each narrative specifies the ORDER of clips and a one-sentence
     hook per clip.
  4. assemble() takes a narrative + clip metadata and produces the cut plan
     (which clip, which time-range to keep, in which order). The system
     then concatenates the ranges into a single MP4.

Narratives examples:
  - "Day in the life" (chronological)
  - "Lesson learned" (problem → discovery → moral)
  - "Travel reel" (arrival → highlights → reflection)
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class ClipBite(BaseModel):
    clip_id: str
    start: float
    end: float
    text: str = ""
    reason: str = ""


class Narrative(BaseModel):
    id: str
    name: str
    genre: str = ""               # e.g. "lesson learned", "travel reel"
    logline: str = ""
    sequence: list[ClipBite] = Field(default_factory=list)
    estimated_duration: float = 0.0


class NarrativeSet(BaseModel):
    narratives: list[Narrative] = Field(default_factory=list)


def new_clip_id() -> str:
    return f"clip_{secrets.token_hex(4)}"


_SYSTEM = """You are a vlog editor. You're given the transcripts of N short
clips (could be travel, daily life, tutorial fragments, anything). The clips
have no single narrative — your job is to find 3-5 DIFFERENT narratives that
could be constructed from these clips.

For each narrative:
  - Give it a name and a genre (e.g. "lesson learned", "travel reel",
    "day in the life", "before/after", "behind-the-scenes").
  - Choose an ORDER of clips that supports the narrative (you can skip clips
    that don't fit; you can use the same clip in multiple narratives).
  - For each chosen clip, pick a sub-range (start, end in seconds) of the
    transcript text that you want to use. Keep ranges short (3-15s each).
  - Give a one-sentence reason why this clip in this position serves the story.

Reply with ONLY a JSON object:

{
  "narratives": [
    {
      "id": "n1",
      "name": "From doubt to clarity",
      "genre": "lesson learned",
      "logline": "I went into the week unsure and came out with three concrete next steps.",
      "sequence": [
        { "clip_id": "clip_xxxx", "start": 0.0, "end": 8.5, "reason": "opening doubt" },
        { "clip_id": "clip_yyyy", "start": 2.3, "end": 11.0, "reason": "trying the approach" },
        ...
      ]
    },
    ...
  ]
}

Order narratives from most-compelling to least."""


async def propose(
    clips_with_transcripts: list[dict[str, Any]],
    *,
    target_count: int = 4,
) -> NarrativeSet:
    """clips_with_transcripts: [{id, name, duration, transcript_text, segments}]"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if not clips_with_transcripts:
        return NarrativeSet()

    blocks: list[str] = []
    for c in clips_with_transcripts:
        cid = c.get("id") or ""
        text = (c.get("transcript_text") or "").strip()
        if len(text) > 1500:
            text = text[:1500] + "..."
        segs = c.get("segments") or []
        seg_lines = "\n".join(
            f"    [{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {(s.get('text') or '').strip()}"
            for s in segs[:30]
        )
        blocks.append(
            f"### {cid} — {c.get('name', cid)} ({c.get('duration', 0):.1f}s)\n"
            f"Summary: {text[:200]}\n"
            f"Segments:\n{seg_lines}"
        )
    user_msg = (
        f"Propose up to {target_count} distinct narratives from these {len(clips_with_transcripts)} clips.\n\n"
        + "\n\n".join(blocks)
    )

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.6,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    data: dict[str, Any] = json.loads(resp.choices[0].message.content or "{}")
    raws = data.get("narratives") or []
    narratives: list[Narrative] = []
    valid_clip_ids = {c["id"] for c in clips_with_transcripts}
    for i, n in enumerate(raws):
        seq_in = n.get("sequence") or []
        seq: list[ClipBite] = []
        for s in seq_in:
            cid = str(s.get("clip_id") or "")
            if cid not in valid_clip_ids:
                continue
            try:
                seq.append(ClipBite(
                    clip_id=cid,
                    start=max(0.0, float(s.get("start") or 0.0)),
                    end=max(0.5, float(s.get("end") or 0.0)),
                    text=str(s.get("text") or "").strip(),
                    reason=str(s.get("reason") or "").strip(),
                ))
            except Exception:
                continue
        if not seq:
            continue
        narratives.append(Narrative(
            id=str(n.get("id") or f"n{i + 1}"),
            name=str(n.get("name") or "Narrative").strip(),
            genre=str(n.get("genre") or "").strip(),
            logline=str(n.get("logline") or "").strip(),
            sequence=seq,
            estimated_duration=round(sum(b.end - b.start for b in seq), 2),
        ))
    return NarrativeSet(narratives=narratives)


def assembly_plan(narrative: Narrative, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert a Narrative into a list of `(clip_path, start, end)` instructions
    the renderer can feed to ffmpeg. Drops bites whose clip_id is unknown."""
    clip_by_id = {c["id"]: c for c in clips}
    plan: list[dict[str, Any]] = []
    for b in narrative.sequence:
        c = clip_by_id.get(b.clip_id)
        if not c:
            continue
        plan.append({
            "clip_id": b.clip_id,
            "filename": c["filename"],
            "start": b.start,
            "end": min(b.end, float(c.get("duration") or b.end)),
            "reason": b.reason,
        })
    return plan
