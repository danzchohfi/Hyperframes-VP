"""Match tagged B-roll angles to A-roll soundbites/chapters.

Strategy:
  1. Hand the LLM the chapter/bite text + each angle's (description, tags,
     categories), and ask which angle (if any) fits which bite, plus an
     in/out window inside that angle.
  2. Layer the matches onto a roughcut timeline plan: V1 stays the A-roll
     spine; V2 receives B-roll inserts at the correct timeline offsets.

The result is consumed by the multitrack FCPXML / xmeml builders.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class Placement(BaseModel):
    soundbite_id: str
    angle_index: int
    angle_in: float = 0.0
    angle_out: float = 0.0
    timeline_offset: float = 0.0   # filled in by the planner, not the LLM
    timeline_duration: float = 0.0
    score: int = 70
    reason: str = ""


class PlacementPlan(BaseModel):
    placements: list[Placement] = Field(default_factory=list)
    skipped_angles: list[int] = Field(default_factory=list)


_SYSTEM = """You are an editor inserting B-roll over an A-roll spine.

Inputs:
  - Soundbites: each has a topic, score, and quoted text.
  - B-roll angles: each has a description, summary, tags, categories,
    quality flag ('ok' is usable; 'shaky'/'blurry'/'dark' should be skipped).

For each soundbite, decide whether a B-roll angle visually supports the line.
Pick at most ONE angle per soundbite. You may pick the same angle for multiple
soundbites if you set in/out points to non-overlapping windows. Skip
soundbites that don't need B-roll (let the A-roll show).

Choose in/out points conservatively: 1.5-3 seconds per insert, never longer
than the soundbite. Don't pick angles flagged shaky/blurry/dark.

Reply with ONLY a JSON object:

{
  "placements": [
    {
      "soundbite_id": "sb3",
      "angle_index": 1,
      "angle_in": 0.5,
      "angle_out": 2.5,
      "score": 85,                  // confidence 0-100
      "reason": "matches the topic of <topic>"
    }
  ]
}

Order placements by soundbite occurrence."""


async def match(
    soundbites: list[dict[str, Any]],
    angles: list[dict[str, Any]],
) -> PlacementPlan:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    usable_angles = []
    for a in angles:
        tags = a.get("tags") or {}
        qc = a.get("quality_check") or {}
        # bail on bad quality from either tags (LLM-vision) or quality_check (OpenCV)
        if tags.get("quality") and tags["quality"] not in ("ok",):
            continue
        if qc.get("quality") and qc["quality"] not in ("ok",):
            continue
        usable_angles.append(a)

    if not usable_angles or not soundbites:
        return PlacementPlan()

    bites_text = "\n".join(
        f"- {b['id']} [{b.get('topic', '?')}] (score {b.get('score', 0)}, "
        f"{b['end'] - b['start']:.1f}s): {b.get('summary') or b['text'][:160]}"
        for b in soundbites
    )
    angles_text = "\n".join(
        _format_angle(i, a) for i, a in enumerate(usable_angles)
    )

    user_msg = (
        f"Soundbites:\n{bites_text}\n\n"
        f"B-roll angles:\n{angles_text}\n"
    )

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    data: dict[str, Any] = json.loads(resp.choices[0].message.content or "{}")

    placements: list[Placement] = []
    angle_indexes_used: set[int] = set()
    bite_by_id = {b["id"]: b for b in soundbites}
    for p in data.get("placements") or []:
        sid = str(p.get("soundbite_id") or "")
        if sid not in bite_by_id:
            continue
        try:
            ai = int(p.get("angle_index"))
        except (TypeError, ValueError):
            continue
        if ai < 0 or ai >= len(angles):
            continue
        a_dur = float(angles[ai].get("duration") or 0.0)
        a_in = max(0.0, float(p.get("angle_in") or 0.0))
        a_out = float(p.get("angle_out") or a_in + 2.0)
        a_out = min(a_dur, max(a_in + 0.6, a_out))
        if a_out - a_in < 0.5:
            continue
        bite = bite_by_id[sid]
        bite_dur = max(0.5, float(bite["end"]) - float(bite["start"]))
        # never make the insert longer than the bite
        if a_out - a_in > bite_dur:
            a_out = a_in + bite_dur
        placements.append(Placement(
            soundbite_id=sid,
            angle_index=ai,
            angle_in=a_in,
            angle_out=a_out,
            score=int(p.get("score") or 70),
            reason=str(p.get("reason") or ""),
        ))
        angle_indexes_used.add(ai)

    skipped = [i for i in range(len(angles)) if i not in angle_indexes_used]
    return PlacementPlan(placements=placements, skipped_angles=skipped)


def _format_angle(idx: int, a: dict[str, Any]) -> str:
    tags = a.get("tags") or {}
    parts = [
        f"[{idx}] {a.get('name', f'Angle {idx + 1}')} ({a.get('duration', 0):.1f}s)",
    ]
    if tags.get("summary"):
        parts.append(f"summary: {tags['summary']}")
    if tags.get("description"):
        parts.append(f"desc: {tags['description']}")
    if tags.get("tags"):
        parts.append(f"tags: {', '.join(tags['tags'][:8])}")
    if tags.get("categories"):
        parts.append(f"categories: {', '.join(tags['categories'])}")
    if tags.get("quality") and tags["quality"] != "ok":
        parts.append(f"!quality={tags['quality']}")
    return "  ".join(parts)


def overlay_plan(
    plan: PlacementPlan,
    soundbites: list[dict[str, Any]],
    aroll_keep: list[tuple[float, float]],
) -> PlacementPlan:
    """Project each placement onto the rough-cut timeline.

    aroll_keep is the source-time keep ranges for the A-roll. We compute, for
    each kept range, what timeline offset it lands at, then attach the
    matching placements to those offsets.
    """
    bite_by_id = {b["id"]: b for b in soundbites}

    # Map source time → timeline time
    boundaries = []
    cursor = 0.0
    for s, e in aroll_keep:
        boundaries.append((s, e, cursor))
        cursor += e - s

    out: list[Placement] = []
    for pl in plan.placements:
        bite = bite_by_id.get(pl.soundbite_id)
        if not bite:
            continue
        bs = float(bite["start"])
        be = float(bite["end"])
        # find which boundary the bite falls in
        timeline_in = None
        for s, e, base in boundaries:
            if bs >= s and bs < e:
                timeline_in = base + (bs - s)
                break
        if timeline_in is None:
            # bite was cut out → skip
            continue
        timeline_out = None
        for s, e, base in boundaries:
            if be > s and be <= e:
                timeline_out = base + (be - s)
                break
        if timeline_out is None:
            continue
        # Center the B-roll insert in the middle of the bite duration
        bite_tl_dur = max(0.6, timeline_out - timeline_in)
        insert_dur = min(pl.angle_out - pl.angle_in, bite_tl_dur)
        if insert_dur <= 0:
            continue
        offset = timeline_in + (bite_tl_dur - insert_dur) / 2.0
        out.append(Placement(
            soundbite_id=pl.soundbite_id,
            angle_index=pl.angle_index,
            angle_in=pl.angle_in,
            angle_out=pl.angle_in + insert_dur,
            timeline_offset=offset,
            timeline_duration=insert_dur,
            score=pl.score,
            reason=pl.reason,
        ))

    out.sort(key=lambda x: x.timeline_offset)
    # Drop overlapping placements (keep higher score)
    dedup: list[Placement] = []
    for pl in out:
        if dedup and pl.timeline_offset < dedup[-1].timeline_offset + dedup[-1].timeline_duration:
            if pl.score > dedup[-1].score:
                dedup[-1] = pl
            continue
        dedup.append(pl)
    return PlacementPlan(placements=dedup, skipped_angles=plan.skipped_angles)
