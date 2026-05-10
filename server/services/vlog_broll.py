"""Optional B-roll layer over a vlog assembly.

Given an assembled vlog timeline (a list of {clip_id, start, end, ...}) and
a project's tagged B-roll angles, ask the LLM to choose which angle covers
each spot in the vlog spine. Return a placement plan compatible with the
existing `broll_match.PlacementPlan` shape so multitrack FCPXML / xmeml /
ffmpeg burn-in pathways already work.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from . import broll_match


_SYSTEM = """You insert B-roll under a vlog narrative. The narrative is a
sequence of CLIP BITES (each a (clip_id, start, end) range from a different
source clip). You also have a list of TAGGED B-ROLL ANGLES with descriptions.

For each bite that visually benefits from B-roll, pick at most one angle and
specify a window (angle_in, angle_out, 1.5-3s) and a timeline_offset where
the insert should land within the bite's local timeline.

Reply with ONLY a JSON object:

{
  "placements": [
    {
      "bite_index": 0,
      "angle_index": 1,
      "angle_in": 0.5,
      "angle_out": 2.5,
      "timeline_offset": 1.2,
      "score": 80,
      "reason": "shows the surf"
    }
  ]
}

Skip bites that don't need B-roll. Don't pick angles flagged shaky/blurry/dark."""


async def match_vlog(
    narrative_sequence: list[dict[str, Any]],
    angles: list[dict[str, Any]],
) -> broll_match.PlacementPlan:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    usable_angles = [a for a in angles
                     if not (a.get("tags", {}).get("quality") and a["tags"]["quality"] != "ok")
                     and not (a.get("quality_check", {}).get("quality") and a["quality_check"]["quality"] != "ok")]

    if not usable_angles or not narrative_sequence:
        return broll_match.PlacementPlan()

    bites_text = "\n".join(
        f"- bite {i}: {b.get('reason', 'no reason')} ({b.get('end', 0) - b.get('start', 0):.1f}s)"
        for i, b in enumerate(narrative_sequence)
    )
    angles_text = "\n".join(broll_match._format_angle(i, a) for i, a in enumerate(angles))

    user_msg = f"Bites:\n{bites_text}\n\nB-roll angles:\n{angles_text}"

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
    data = json.loads(resp.choices[0].message.content or "{}")

    # Convert bite-relative offsets into vlog-timeline offsets
    cum: list[float] = []
    cur = 0.0
    for b in narrative_sequence:
        cum.append(cur)
        cur += float(b.get("end", 0)) - float(b.get("start", 0))

    placements: list[broll_match.Placement] = []
    for p in data.get("placements") or []:
        try:
            bi = int(p.get("bite_index"))
            ai = int(p.get("angle_index"))
        except (TypeError, ValueError):
            continue
        if bi < 0 or bi >= len(narrative_sequence):
            continue
        if ai < 0 or ai >= len(angles):
            continue
        a_in = max(0.0, float(p.get("angle_in") or 0.0))
        a_out = float(p.get("angle_out") or a_in + 2.0)
        a_out = max(a_in + 0.5, a_out)
        bite_dur = float(narrative_sequence[bi].get("end", 0)) - float(narrative_sequence[bi].get("start", 0))
        local_off = max(0.0, min(bite_dur - 0.4, float(p.get("timeline_offset") or 0.4)))
        global_off = cum[bi] + local_off
        ins_dur = min(a_out - a_in, max(0.4, bite_dur - local_off))
        placements.append(broll_match.Placement(
            soundbite_id=f"bite_{bi}",
            angle_index=ai,
            angle_in=a_in,
            angle_out=a_in + ins_dur,
            timeline_offset=global_off,
            timeline_duration=ins_dur,
            score=int(p.get("score") or 70),
            reason=str(p.get("reason") or ""),
        ))
    used_angle_indexes = {pl.angle_index for pl in placements}
    skipped = [i for i in range(len(angles)) if i not in used_angle_indexes]
    return broll_match.PlacementPlan(placements=placements, skipped_angles=skipped)
