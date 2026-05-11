"""Vlog assembly as a pure service.

Reusable from /vlog/assemble and from /vlog/auto-pipeline. Returns a dict
with the final export name + URL + bytes + (optional) social_copy payload.

Input:
  - project_id (so we can read/write JSON artifacts on disk)
  - narrative_id (must exist in vlog_narratives.json)
  - options: aspect, loudnorm, apply_brand, chapter_cards, auto_social_copy
  - emit (optional): a callable used to push render progress events to the
    SSE bus when called from inside a JobContext

Output: same shape as the prior endpoint response.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import storage
from . import ffmpeg as ff
from . import composer
from . import render as render_svc
from . import vlog as vlog_svc
from . import social_copy as social_svc
from .brand import BrandBook
from .events import emit_stage


ProgressFn = Callable[[str, float | None, str | None], None]
OnRenderEvent = Callable[[dict], None] | Callable[[dict], Awaitable[None]]


class AssembleResult(dict):
    pass


async def run(
    *,
    project_id: str,
    narrative_id: str,
    aspect: str = "9:16",
    loudnorm: bool = True,
    apply_brand: bool = True,
    chapter_cards: bool = True,
    auto_social_copy: bool = True,
    on_render_event: OnRenderEvent | None = None,
) -> dict[str, Any]:
    pid = project_id
    state = storage.load(pid)
    pdir = storage.project_dir(pid)
    if not state.clips:
        raise RuntimeError("no clips")
    npath = pdir / "vlog_narratives.json"
    if not npath.exists():
        raise RuntimeError("run /vlog/narratives first")

    data = storage.read_json(pid, "vlog_narratives.json")
    narrative_dict = next((n for n in data["narratives"] if n["id"] == narrative_id), None)
    if not narrative_dict:
        raise RuntimeError(f"narrative {narrative_id} not found")
    narrative = vlog_svc.Narrative.model_validate(narrative_dict)

    # Build snapping-aware assembly plan
    clip_transcripts: dict[str, dict] = {}
    for c in state.clips:
        tp = pdir / "clips" / f"{c['id']}_transcript.json"
        if tp.exists():
            try:
                clip_transcripts[c["id"]] = json.loads(tp.read_text())
            except Exception:
                continue
    plan = vlog_svc.assembly_plan(narrative, state.clips, clip_transcripts=clip_transcripts)
    if not plan:
        raise RuntimeError("narrative didn't resolve to any clips")

    items: list[tuple[Path, float, float]] = []
    for p in plan:
        items.append((pdir / "clips" / p["filename"], float(p["start"]), float(p["end"])))

    presets = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
    w, h = presets.get(aspect, (1920, 1080))

    raw = pdir / "exports" / f"{state.name.replace(' ', '_')}-vlog-{narrative.id}-raw.mp4"
    raw.parent.mkdir(exist_ok=True)
    emit_stage(pid, "vlog_assemble", "running", f"{len(items)} segments")
    await ff.concat_segments_from_multiple(items, raw, width=w, height=h, loudnorm=loudnorm)

    final_url = f"/api/projects/{pid}/exports/{raw.name}"
    final_path = raw

    # Optional brand wrap via Hyperframes composer
    if apply_brand:
        unified = vlog_svc.build_vlog_transcript(narrative, clip_transcripts) if clip_transcripts else None
        chapters = vlog_svc.build_chapter_markers(narrative, state.clips) if chapter_cards else None

        brand = (
            BrandBook.model_validate(storage.read_json(pid, "brand.json"))
            if state.has_brand else BrandBook()
        )
        comp_parent = pdir / f"vlog_comp_{narrative.id}"
        comp_parent.mkdir(exist_ok=True)
        try:
            comp_dir = composer.build_composition(
                project_dir=comp_parent,
                video_path=raw,
                video_duration=await ff.duration(raw),
                transcript=unified,
                brand=brand,
                aspect=aspect,
                chapters=chapters,
            )
            emit_stage(pid, "vlog_assemble", "running", "rendering brand wrap")

            def _forward(ev: dict) -> None:
                pct = ev.get("progress")
                emit_stage(pid, "vlog_assemble", "running",
                           f"render {pct}% · {ev.get('label', '')}",
                           progress=float(pct) / 100.0 if pct is not None else None)

            rendered = await render_svc.render(
                comp_dir,
                output_dir=pdir / "exports",
                name=f"{state.name.replace(' ', '_')}-vlog-{narrative.id}",
                on_event=on_render_event or _forward,
                project_id=None,
            )
            final_path = rendered
            final_url = f"/api/projects/{pid}/exports/{rendered.name}"
            try:
                raw.unlink()
                shutil.rmtree(comp_parent)
            except Exception:
                pass
        except Exception as e:
            emit_stage(pid, "vlog_assemble", "error", f"brand wrap failed: {e}")
            final_path = raw
            final_url = f"/api/projects/{pid}/exports/{raw.name}"

    storage.append_render_history(pid, name=final_path.name, kind="vlog",
                                  url=final_url,
                                  bytes=final_path.stat().st_size,
                                  extra={"narrative": narrative.name,
                                         "genre": narrative.genre,
                                         "clips": len(items),
                                         "branded": apply_brand})
    emit_stage(pid, "vlog_assemble", "done",
               f"{final_path.name} · {final_path.stat().st_size // 1024} KB")

    social_payload: dict[str, Any] | None = None
    if auto_social_copy:
        try:
            unified = vlog_svc.build_vlog_transcript(narrative, clip_transcripts) if clip_transcripts else None
            full_text = " ".join(
                (s.get("text") or "") for s in (unified or {}).get("segments") or []
            ).strip()
            if full_text:
                social = await social_svc.generate(
                    transcript_text=full_text,
                    title=narrative.name,
                    logline=narrative.logline,
                    brand_name=state.name,
                    language=(unified or {}).get("language") or "pt",
                )
                storage.write_json(pid, "social_copy.json", social.model_dump())
                social_payload = social.model_dump()
        except Exception as e:
            emit_stage(pid, "vlog_assemble", "error", f"social copy failed: {e}")

    return {
        "narrative": narrative.model_dump(),
        "plan": plan,
        "export": final_path.name,
        "url": final_url,
        "bytes": final_path.stat().st_size,
        "branded": apply_brand,
        "social_copy": social_payload,
    }
