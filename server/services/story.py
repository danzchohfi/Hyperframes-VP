"""LLM proposes a story structure from selected soundbites.

Default structure: four-act (Intro, Conflict, Resolution, Conclusion).
Editor can also pass a custom structure name like "hero_journey", "explainer",
"testimonial", "before_after", etc. — the LLM adapts.
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
    summary: str = ""
    soundbite_ids: list[str] = Field(default_factory=list)
    transition_note: str = ""


class Story(BaseModel):
    structure: str = "four_act"
    title: str = ""
    logline: str = ""
    chapters: list[Chapter] = Field(default_factory=list)


_STRUCTURES = {
    "four_act": "Introduction, Conflict, Resolution, Conclusion (4 chapters)",
    "hero_journey": "Call, Trial, Revelation, Return (4 chapters)",
    "explainer": "Hook, Context, Steps, Takeaway (4 chapters)",
    "testimonial": "Before, Turning Point, After, Recommendation (4 chapters)",
    "before_after": "Before, Decision, Process, After (4 chapters)",
}


_SYSTEM = """You arrange interview soundbites into a coherent story.

You receive a list of soundbites with ids, topics, and quoted text. Build a
story by ordering soundbites into chapters that follow the requested
structure. Each soundbite is used at most once. You may skip soundbites that
don't fit. Aim for an emotionally satisfying arc.

Reply with ONLY a JSON object:

{
  "title": "<concise 3-7 word video title>",
  "logline": "<one-sentence summary of the story>",
  "chapters": [
    {
      "id": "c1",
      "name": "<chapter name from the requested structure>",
      "summary": "<what happens in this chapter, 1-2 sentences>",
      "soundbite_ids": ["sb3", "sb1", "sb7"],   // ordered for the timeline
      "transition_note": "<short cue: 'beat', 'fade', 'B-roll bridge', etc.>"
    }
  ]
}

Make the chapter ordering deliberate. Stronger soundbites belong at the open and close."""


async def propose(
    soundbites: list[dict],
    topics: list[dict],
    *,
    structure: str = "four_act",
    style_note: str | None = None,
) -> Story:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    plan_desc = _STRUCTURES.get(structure, structure)

    bites_text = "\n".join(
        f"- {b['id']} [{b['topic']}] (score {b.get('score', 0)}): {b.get('summary') or b['text'][:120]}"
        for b in soundbites
    )
    topics_text = "\n".join(f"- {t['id']}: {t['name']} — {t.get('summary', '')}" for t in topics)
    user_msg = (
        f"Structure: {structure} → {plan_desc}\n\n"
        f"Topics:\n{topics_text}\n\n"
        f"Soundbites:\n{bites_text}\n"
    )
    if style_note:
        user_msg += f"\nStyle note: {style_note}"

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.4,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    data: dict[str, Any] = json.loads(resp.choices[0].message.content or "{}")
    chapters: list[Chapter] = []
    for c in data.get("chapters") or []:
        chapters.append(
            Chapter(
                id=str(c.get("id") or f"c{len(chapters) + 1}"),
                name=str(c.get("name") or "Chapter").strip(),
                summary=str(c.get("summary") or "").strip(),
                soundbite_ids=[str(x) for x in (c.get("soundbite_ids") or [])],
                transition_note=str(c.get("transition_note") or "").strip(),
            )
        )

    return Story(
        structure=structure,
        title=str(data.get("title") or "").strip(),
        logline=str(data.get("logline") or "").strip(),
        chapters=chapters,
    )
