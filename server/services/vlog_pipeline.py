"""One-click vlog pipeline.

Takes a project that already has clips uploaded, then:

  1. Transcribes any clips that are missing transcripts.
  2. Optional: cross-clip face identity clustering (so we know who shows up).
  3. Proposes narratives via the LLM.
  4. Picks the top narrative and assembles it (with brand wrap by default).

Designed to run inside a JobContext so the UI gets live progress.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import jobs as jobs_svc
from . import whisper as whisper_svc
from . import vlog as vlog_svc
from . import face_identity as face_id_svc
from .. import storage


async def run(
    ctx: jobs_svc.JobContext,
    *,
    language: str | None = None,
    aspect: str = "9:16",
    apply_brand: bool = True,
    chapter_cards: bool = True,
    do_face_clustering: bool = True,
) -> dict[str, Any]:
    pid = ctx.project_id
    state = storage.load(pid)
    pdir = storage.project_dir(pid)
    if not state.clips:
        raise RuntimeError("upload clips first")

    out: dict[str, Any] = {"clip_count": len(state.clips)}

    # 1. Transcribe missing
    pending = [c for c in state.clips if not c.get("has_transcript")]
    if pending:
        for i, c in enumerate(pending):
            ctx.check_cancel()
            ctx.progress(0.05 + 0.35 * (i / len(pending)),
                         f"transcribing {c['name']} ({i + 1}/{len(pending)})")
            src = pdir / "clips" / c["filename"]
            if not src.exists():
                continue
            try:
                result = await whisper_svc.transcribe(src, language=language)
                (pdir / "clips" / f"{c['id']}_transcript.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2)
                )
                c["has_transcript"] = True
                c["transcript_text"] = (result.get("text") or "")[:300]
                c["language"] = result.get("language")
                state = storage.load(pid)  # reload to get persisted clip mutations
                # Actually reload + mutate
                for sc in state.clips:
                    if sc["id"] == c["id"]:
                        sc.update(c)
                storage.save(state)
            except Exception as e:
                ctx.log(f"clip {c['id']} transcribe failed: {e}", level="error")
    else:
        ctx.progress(0.40, "all clips already transcribed")

    # 2. Face clustering (optional, ~few seconds)
    if do_face_clustering:
        ctx.progress(0.45, "fingerprinting faces")
        try:
            fingerprints: dict[str, list[list[float]]] = {}
            for c in state.clips:
                p = pdir / "clips" / c["filename"]
                if p.exists():
                    fingerprints[f"clip:{c['id']}:{c.get('name', c['id'])}"] = (
                        face_id_svc.fingerprint_clip(p)
                    )
            if any(fingerprints.values()):
                identities = face_id_svc.cluster_identities(fingerprints)
                storage.write_json(pid, "face_identities.json", identities)
                # annotate clips
                for c in state.clips:
                    key = f"clip:{c['id']}:{c.get('name', c['id'])}"
                    c["face_clusters"] = identities["presence"].get(key, [])
                storage.save(state)
                out["people_detected"] = len(identities["clusters"])
                ctx.log(f"detected {len(identities['clusters'])} people across clips")
        except Exception as e:
            ctx.log(f"face clustering skipped: {e}", level="warn")

    # 3. Build narrative payload + propose
    ctx.progress(0.55, "proposing narratives")
    payload = []
    for c in state.clips:
        tp = pdir / "clips" / f"{c['id']}_transcript.json"
        if not tp.exists():
            continue
        try:
            t = json.loads(tp.read_text())
        except Exception:
            continue
        payload.append({
            "id": c["id"],
            "name": c.get("name"),
            "duration": c.get("duration") or 0.0,
            "transcript_text": (t.get("text") or "")[:1500],
            "segments": t.get("segments") or [],
        })
    if not payload:
        raise RuntimeError("no transcripts to analyze")
    narratives = await vlog_svc.propose(payload, target_count=4)
    storage.write_json(pid, "vlog_narratives.json", narratives.model_dump())
    out["narratives"] = [{"id": n.id, "name": n.name, "genre": n.genre,
                          "logline": n.logline,
                          "estimated_duration": n.estimated_duration,
                          "clips_used": len(n.sequence)} for n in narratives.narratives]
    if not narratives.narratives:
        raise RuntimeError("LLM produced no narratives")

    # 4. Assemble the top narrative
    top = narratives.narratives[0]
    ctx.log(f"assembling top narrative: {top.name} ({top.genre})")
    ctx.progress(0.70, f"assembling: {top.name}")
    out["chosen_narrative"] = top.model_dump()
    out["assembled_via"] = "/vlog/assemble"
    out["next_step"] = (
        f"POST /api/projects/{pid}/vlog/assemble with "
        f"{{narrative_id: '{top.id}', aspect: '{aspect}', apply_brand: {apply_brand}}}"
    )
    ctx.progress(1.0, "narratives ready — pick one and POST /vlog/assemble")
    return out
