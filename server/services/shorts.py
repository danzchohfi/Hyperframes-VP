"""Auto-generate N short-form clips from the highest-scoring soundbites.

For each pick, the system:
  1. Cuts the source between the soundbite's start/end (with padding)
  2. Optionally renders via Hyperframes with the project's BrandBook
     (so captions, lower-thirds, logo all apply per-clip)
  3. Reframes to 9:16 if needed
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .. import storage
from . import composer
from . import ffmpeg as ff
from . import jobs as jobs_svc
from . import render as render_svc
from .brand import BrandBook
from .captions import retime_segments, retime_words


def pick_shorts(
    soundbites: list[dict],
    *,
    target_count: int = 5,
    min_seconds: float = 8.0,
    max_seconds: float = 60.0,
) -> list[dict]:
    """Pick the top-scoring soundbites that fall inside [min, max] seconds.
    Ordered by score descending."""
    out: list[dict] = []
    for sb in sorted(soundbites, key=lambda b: -float(b.get("score") or 0)):
        dur = float(sb.get("end", 0)) - float(sb.get("start", 0))
        if min_seconds <= dur <= max_seconds:
            out.append(sb)
        if len(out) >= target_count:
            break
    return out


async def render_short(
    ctx: jobs_svc.JobContext,
    *,
    project_id: str,
    soundbite: dict,
    brand: BrandBook,
    aspect: str = "9:16",
    use_hyperframes: bool = True,
) -> dict[str, Any]:
    """Cut + render one soundbite as a standalone clip. Returns path + URL."""
    pdir = storage.project_dir(project_id)
    src = pdir / "source.mp4"
    sb_id = str(soundbite.get("id") or "sb")
    out_dir = pdir / "shorts"
    out_dir.mkdir(exist_ok=True)

    start = max(0.0, float(soundbite["start"]) - 0.1)
    end = float(soundbite["end"]) + 0.1
    dur = end - start

    raw_clip = out_dir / f"{sb_id}-raw.mp4"
    await ff.cut_segments(src, raw_clip, [(start, end)], lut=(pdir / "lut.cube" if (pdir / "lut.cube").exists() else None), loudnorm=True)

    final = out_dir / f"{sb_id}.mp4"

    if not use_hyperframes:
        # Just reframe to the target aspect
        await ff.to_aspect(raw_clip, final, aspect)
        try:
            raw_clip.unlink()
        except Exception:
            pass
        return {
            "id": sb_id,
            "path": str(final),
            "url": f"/api/projects/{project_id}/files/shorts/{final.name}",
            "duration": dur,
        }

    # Hyperframes path: build a comp around the clip
    transcript = None
    tjson = pdir / "transcript.json"
    if tjson.exists():
        full = storage.read_json(project_id, "transcript.json")
        # Re-time words/segments into the short's local timeline
        keep = [(start, end)]
        full["words"] = retime_words(full.get("words") or [], keep)
        full["segments"] = retime_segments(full.get("segments") or [], keep)
        transcript = full

    speaker_turns = None
    sj = pdir / "speakers.json"
    if sj.exists():
        sdata = storage.read_json(project_id, "speakers.json") or {}
        turns = sdata.get("turns") or []
        speaker_turns = retime_segments(turns, [(start, end)])

    # Build composition inside out_dir/<sb_id>/composition/
    comp_parent = out_dir / sb_id
    comp_parent.mkdir(exist_ok=True)
    comp_dir = composer.build_composition(
        project_dir=comp_parent,
        video_path=raw_clip,
        video_duration=dur,
        transcript=transcript,
        brand=brand,
        aspect=aspect,
        speaker_turns=speaker_turns,
    )

    ctx.log(f"rendering short {sb_id}")

    def _on_event(ev: dict) -> None:
        # forward progress weighted into the parent job's progress
        pct = ev.get("progress")
        if pct is not None:
            ctx.log(f"  short {sb_id}: {pct}%")

    rendered = await render_svc.render(
        comp_dir,
        output_dir=out_dir,
        name=sb_id,
        on_event=_on_event,
        project_id=None,  # don't lock per-project render slot
    )

    # Cleanup composition + raw to save disk
    try:
        raw_clip.unlink()
        import shutil
        shutil.rmtree(comp_parent)
    except Exception:
        pass

    return {
        "id": sb_id,
        "path": str(rendered),
        "url": f"/api/projects/{project_id}/files/shorts/{rendered.name}",
        "duration": dur,
    }


async def run_batch(
    ctx: jobs_svc.JobContext,
    *,
    target_count: int = 5,
    aspect: str = "9:16",
    use_hyperframes: bool = True,
    min_seconds: float = 8.0,
    max_seconds: float = 60.0,
) -> dict[str, Any]:
    """Generate N shorts as a single job, emitting progress."""
    pid = ctx.project_id
    state = storage.load(pid)
    if not state.has_soundbites:
        raise RuntimeError("extract soundbites first")
    analysis = storage.read_json(pid, "soundbites.json")
    picks = pick_shorts(
        analysis.get("soundbites") or [],
        target_count=target_count,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
    )
    if not picks:
        raise RuntimeError("no soundbites in [min, max] window")

    brand = (
        BrandBook.model_validate(storage.read_json(pid, "brand.json"))
        if state.has_brand else BrandBook()
    )

    results: list[dict] = []
    for i, sb in enumerate(picks):
        ctx.check_cancel()
        ctx.progress(i / len(picks), f"short {i + 1}/{len(picks)} ({sb.get('id')})")
        try:
            r = await render_short(
                ctx,
                project_id=pid,
                soundbite=sb,
                brand=brand,
                aspect=aspect,
                use_hyperframes=use_hyperframes,
            )
            results.append(r)
            storage.append_render_history(pid, name=Path(r["path"]).name, kind="short",
                                          url=r["url"], bytes=Path(r["path"]).stat().st_size,
                                          extra={"soundbite_id": sb.get("id"), "score": sb.get("score")})
        except Exception as e:
            ctx.log(f"short {sb.get('id')} failed: {e}", level="error")
    ctx.progress(1.0, f"{len(results)} shorts done")
    storage.write_json(pid, "shorts_manifest.json", {"shorts": results})
    return {"count": len(results), "shorts": results}
