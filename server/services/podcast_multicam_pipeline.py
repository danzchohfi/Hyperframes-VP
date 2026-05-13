"""One-click podcast multicam pipeline.

Orchestrates every step from "user dropped source + angles" to
"FCPXML ready to open in Final Cut" as a single async job:

  1. Transcribe (Whisper, compact-audio path)
  2. Detect speakers (diarization)
  3. Detect chapters
  4. Detect silences + fillers
  5. Apply edits (cuts + LUT)         → graded.mp4
  6. Level speakers                    → exports/<name>-levelled.mp4
  7. Multicam sync (audio cross-corr)  → angle.audio_offset
  8. Multicam pick (camera per turn)   → camera_plan.json
  9. Multicam render                   → exports/<name>-multicam.mp4
 10. FCPXML export                     → exports/<name>.fcpxml

Each step emits progress + a human label to the SSE bus so the UI
shows "Step 7 of 10 · Sincronizando áudio das câmeras…".

Designed for non-technical users: every step is idempotent (skips if
already done), errors in non-critical steps (chapters, social) only
warn, and the final result is a single JSON with the URLs of all
outputs so the UI can show "Tudo pronto" with download buttons.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import jobs as jobs_svc
from . import ffmpeg as ff
from . import whisper as whisper_svc
from . import speakers as speakers_svc
from . import chapters as chapters_svc
from . import fillers as fillers_svc
from . import silence as silence_svc
from . import speaker_levels as levels_svc
from . import social_copy as social_svc
from . import audio_sync as audio_sync_svc
from . import camera_picker as camera_picker_svc
from . import multicam_render as mc_render_svc
from . import fcpxml as fcpxml_svc
from .brand import BrandBook
from .. import storage


# Step weights — must sum to ~1.0. Bigger numbers = the step takes longer
# in wall time on a typical podcast (transcribe / render are the big ones).
STEP_WEIGHTS = {
    "transcribe":     0.18,
    "speakers":       0.06,
    "chapters":       0.03,
    "silences":       0.04,
    "apply_edits":    0.10,
    "level_speakers": 0.06,
    "multicam_sync":  0.05,
    "multicam_pick":  0.05,
    "multicam_render":0.35,
    "fcpxml_export":  0.02,
    "social_copy":    0.06,
}


def _step_starts() -> dict[str, float]:
    """Map each step to its cumulative start fraction (0.0..1.0)."""
    starts: dict[str, float] = {}
    acc = 0.0
    for name, w in STEP_WEIGHTS.items():
        starts[name] = acc
        acc += w
    return starts


_STARTS = _step_starts()


async def run(ctx: jobs_svc.JobContext, *, language: str | None = None) -> dict[str, Any]:
    pid = ctx.project_id
    state = storage.load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise RuntimeError("Suba o vídeo de origem antes de rodar o pipeline.")

    out: dict[str, Any] = {"steps": [], "outputs": {}, "warnings": []}
    angles_count = len(state.angles)

    def step(name: str, label: str, sub: float = 0.0) -> None:
        """Sub is 0..1 within the step. Combined with STEP_WEIGHTS."""
        base = _STARTS.get(name, 0.0)
        weight = STEP_WEIGHTS.get(name, 0.0)
        ctx.progress(min(0.999, base + weight * max(0.0, min(1.0, sub))), label)

    def done(name: str, summary: str = "") -> None:
        out["steps"].append({"name": name, "status": "ok", "summary": summary})

    def warn(name: str, msg: str) -> None:
        out["warnings"].append(f"{name}: {msg}")
        ctx.log(f"{name} skipped: {msg}", level="warn")
        out["steps"].append({"name": name, "status": "warn", "summary": msg})

    # ── 1. transcribe ─────────────────────────────────────────────────
    step("transcribe", "Transcrevendo áudio com Whisper…", 0.05)
    if not state.has_transcript:
        try:
            result = await whisper_svc.transcribe(src, language=language)
            storage.write_json(pid, "transcript.json", result)
            state.has_transcript = True
            storage.save(state)
            done("transcribe", f"{len(result.get('words') or [])} palavras")
        except Exception as e:
            raise RuntimeError(f"transcrição falhou: {e}")
    else:
        done("transcribe", "já existia")
    transcript = storage.read_json(pid, "transcript.json")
    ctx.check_cancel()

    # ── 2. speakers ───────────────────────────────────────────────────
    step("speakers", "Detectando speakers (quem fala quando)…", 0.05)
    try:
        spk = await speakers_svc.diarize(src, transcript.get("words") or [], backend="auto")
        storage.write_json(pid, "speakers.json", spk)
        state.has_speakers = True
        storage.save(state)
        done("speakers", f"{spk['stats']['speaker_count']} voz(es)")
    except Exception as e:
        warn("speakers", str(e))
        spk = None
    ctx.check_cancel()

    # ── 3. chapters ───────────────────────────────────────────────────
    step("chapters", "Detectando capítulos do podcast…", 0.05)
    try:
        chap = await chapters_svc.detect(transcript)
        storage.write_json(pid, "chapters.json", {
            "chapters": [c.model_dump() for c in chap.chapters],
            "youtube_markdown": chapters_svc.to_youtube_markdown(chap.chapters),
        })
        state.has_chapters = True
        storage.save(state)
        done("chapters", f"{len(chap.chapters)} capítulo(s)")
    except Exception as e:
        warn("chapters", str(e))
    ctx.check_cancel()

    # ── 4. silences + fillers ─────────────────────────────────────────
    step("silences", "Achando silêncios e muletas…", 0.05)
    try:
        silences = await ff.detect_silences(src, noise_db=-32.0, min_silence=0.5)
        dur = state.source_duration or (await ff.duration(src))
        keep = silence_svc.plan_keep_segments(dur, silences)
        storage.write_json(pid, "cuts.json", {
            "options": {"noise_db": -32.0, "min_silence": 0.5, "pad": 0.08},
            "source_duration": dur,
            "silences": [{"start": s, "end": e} for s, e in silences],
            "keep": [{"start": s, "end": e} for s, e in keep],
            "kept_duration": silence_svc.total_kept(keep),
        })
        state.has_cuts = True
        lang = language or transcript.get("language") or "auto"
        filler_ranges = fillers_svc.detect_filler_ranges(
            transcript.get("words") or [], language=lang,
        )
        storage.write_json(pid, "fillers.json", {
            "language": lang,
            "ranges": [{"start": s, "end": e} for s, e in filler_ranges],
            "stats": fillers_svc.stats(filler_ranges),
        })
        state.has_fillers = True
        state.fillers_count = len(filler_ranges)
        storage.save(state)
        done("silences", f"{len(silences)} silêncios · {len(filler_ranges)} muletas")
    except Exception as e:
        warn("silences", str(e))
        keep = None
        filler_ranges = []
    ctx.check_cancel()

    # ── 5. apply edits ────────────────────────────────────────────────
    step("apply_edits", "Aplicando cortes + LUT…", 0.05)
    graded = pdir / "graded.mp4"
    try:
        if keep is None:
            raise RuntimeError("nenhum plano de cortes")
        keep_after = fillers_svc.subtract_ranges(keep, filler_ranges)
        lut = pdir / "lut.cube" if state.has_lut else None
        await ff.cut_segments(src, graded, keep_after, lut=lut, loudnorm=True)
        out["outputs"]["graded"] = {
            "name": graded.name,
            "url": f"/api/projects/{pid}/files/{graded.name}",
            "duration": await ff.duration(graded),
            "bytes": graded.stat().st_size,
        }
        done("apply_edits", f"{out['outputs']['graded']['duration']:.1f}s editado")
    except Exception as e:
        warn("apply_edits", str(e))
    ctx.check_cancel()

    # ── 6. level speakers ─────────────────────────────────────────────
    step("level_speakers", "Nivelando volume entre speakers…", 0.05)
    if spk and graded.exists():
        try:
            levels = await levels_svc.measure_per_speaker(graded, spk["turns"])
            gains = levels_svc.gain_plan(levels, target_dbfs=-18.0)
            af = levels_svc.build_filter(spk["turns"], gains)
            levelled = pdir / "exports" / f"{state.name.replace(' ', '_')}-levelled.mp4"
            levelled.parent.mkdir(exist_ok=True)
            await ff.apply_audio_filter(graded, levelled, af)
            storage.write_json(pid, "speaker_levels.json", {
                "target_dbfs": -18.0, "source": "graded",
                "levels": levels, "gains": gains,
            })
            out["outputs"]["levelled"] = {
                "name": levelled.name,
                "url": f"/api/projects/{pid}/exports/{levelled.name}",
                "bytes": levelled.stat().st_size,
            }
            done("level_speakers", "voz normalizada")
        except Exception as e:
            warn("level_speakers", str(e))
    else:
        warn("level_speakers", "speakers ou graded ausente")
    ctx.check_cancel()

    # ── Multicam steps (only when the project has angles) ────────────
    if angles_count >= 1:
        # ── 7. multicam audio sync ───────────────────────────────────
        step("multicam_sync", f"Sincronizando áudio das {angles_count} câmera(s)…", 0.05)
        try:
            angles_dir = pdir / "angles"
            clips = [(state.name + " (A)", src)]
            for a in state.angles:
                clips.append((a["name"], angles_dir / a["filename"]))
            offsets = await audio_sync_svc.compute_offsets(clips)
            # write back into state
            state_cur = storage.load(pid)
            for entry in offsets:
                for a in state_cur.angles:
                    if a.get("name") == entry["name"]:
                        a["audio_offset"] = entry["offset"]
                        a["audio_offset_score"] = entry.get("score", 0.0)
                        break
            storage.save(state_cur)
            storage.write_json(pid, "multicam_sync.json", {"offsets": offsets})
            done("multicam_sync", f"{len(offsets) - 1} ângulo(s) alinhado(s)")
        except Exception as e:
            warn("multicam_sync", str(e))
        ctx.check_cancel()

        # ── 8. multicam pick ─────────────────────────────────────────
        step("multicam_pick", "Escolhendo a melhor câmera em cada turno…", 0.05)
        try:
            state_cur = storage.load(pid)
            angle_pool: list[dict] = [{
                "name": "source",
                "face_analysis": None,
                "quality_check": None,
            }]
            for a in state_cur.angles:
                angle_pool.append({
                    "name": a.get("name"),
                    "face_analysis": a.get("face_analysis"),
                    "quality_check": a.get("quality_check"),
                })
            if not (pdir / "speakers.json").exists():
                raise RuntimeError("speakers.json ausente")
            turns = (storage.read_json(pid, "speakers.json") or {}).get("turns") or []
            intervals = [(float(t["start"]), float(t["end"])) for t in turns]
            cuts = camera_picker_svc.pick_cameras(
                intervals=intervals, angles=angle_pool, min_dur=1.4,
            )
            storage.write_json(pid, "camera_plan.json", {
                "intervals": "turns",
                "angles": [a["name"] for a in angle_pool],
                "cuts": cuts,
            })
            state_cur.has_camera_plan = True
            storage.save(state_cur)
            done("multicam_pick", f"{len(cuts)} corte(s) de câmera planejado(s)")
        except Exception as e:
            warn("multicam_pick", str(e))
        ctx.check_cancel()

        # ── 9. multicam render ───────────────────────────────────────
        step("multicam_render", "Renderizando vídeo multicam (mais demorado)…", 0.05)
        try:
            if not (pdir / "camera_plan.json").exists():
                raise RuntimeError("camera_plan.json ausente")
            plan = storage.read_json(pid, "camera_plan.json")
            state_cur = storage.load(pid)
            angles_dir = pdir / "angles"
            angle_paths: list[Path] = [src]
            angle_offsets: list[float] = [0.0]
            for a in state_cur.angles:
                angle_paths.append(angles_dir / a["filename"])
                angle_offsets.append(float(a.get("audio_offset") or 0.0))
            mc_out = pdir / "exports" / f"{state.name.replace(' ', '_')}-multicam.mp4"
            mc_out.parent.mkdir(exist_ok=True)
            await mc_render_svc.render(
                project_dir=pdir, source=src,
                angle_paths=angle_paths, angle_offsets=angle_offsets,
                plan=plan["cuts"], out=mc_out,
            )
            out["outputs"]["multicam"] = {
                "name": mc_out.name,
                "url": f"/api/projects/{pid}/exports/{mc_out.name}",
                "bytes": mc_out.stat().st_size,
            }
            done("multicam_render", f"{mc_out.stat().st_size // 1024} KB · {len(plan['cuts'])} cortes")
        except Exception as e:
            warn("multicam_render", str(e))
        ctx.check_cancel()
    else:
        # Skip the multicam block entirely; advance progress to where
        # the next step would be so the bar doesn't stall.
        step("fcpxml_export", "Sem ângulos — pulando passos multicam.", 0.05)

    # ── 10. FCPXML export ────────────────────────────────────────────
    step("fcpxml_export", "Gerando XML pro Final Cut Pro…", 0.05)
    try:
        state_cur = storage.load(pid)
        brand = (
            BrandBook.model_validate(storage.read_json(pid, "brand.json"))
            if state_cur.has_brand else BrandBook()
        )

        keep_for_export: list[tuple[float, float]] | None = None
        if state_cur.has_cuts:
            cuts_json = storage.read_json(pid, "cuts.json")
            keep_for_export = [(seg["start"], seg["end"]) for seg in cuts_json.get("keep") or []]

        chapters_list = (
            (storage.read_json(pid, "chapters.json") or {}).get("chapters")
            if (pdir / "chapters.json").exists() else None
        )
        speakers_list = None
        if (pdir / "speakers.json").exists():
            sp = storage.read_json(pid, "speakers.json")
            speakers_list = sp.get("turns") or sp.get("segments")

        camera_cuts = None
        if (pdir / "camera_plan.json").exists():
            plan = storage.read_json(pid, "camera_plan.json")
            camera_cuts = [
                {"start": float(c.get("start") or 0.0),
                 "end":   float(c.get("end") or 0.0),
                 "angle_index": int(c.get("angle_index") or 0)}
                for c in (plan.get("cuts") or [])
            ] or None

        if angles_count >= 1:
            angles_dir = pdir / "angles"
            angle_paths_xml: list[tuple[str, Path]] = [(state_cur.name + " (A)", src)]
            offsets_xml: list[float] = [0.0]
            for a in state_cur.angles:
                angle_paths_xml.append((a["name"], angles_dir / a["filename"]))
                offsets_xml.append(float(a.get("audio_offset") or 0.0))
            xml = await fcpxml_svc.build_multicam_fcpxml(
                project_name=state_cur.name,
                angles=angle_paths_xml,
                primary_index=0,
                cuts=keep_for_export,
                transcript=transcript,
                angle_offsets=offsets_xml,
                camera_cuts=camera_cuts,
                chapters=chapters_list,
                soundbites=None,
                speakers=speakers_list,
                source_color_profile=state_cur.source_color_profile,
            )
        else:
            xml = await fcpxml_svc.build_single_cam_fcpxml(
                project_name=state_cur.name,
                source=src,
                cuts=keep_for_export,
                transcript=transcript,
                source_color_profile=state_cur.source_color_profile,
            )

        out_path = pdir / "exports" / f"{state_cur.name.replace(' ', '_')}.fcpxml"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(xml, encoding="utf-8")
        out["outputs"]["fcpxml"] = {
            "name": out_path.name,
            "url": f"/api/projects/{pid}/exports/{out_path.name}",
            "bytes": out_path.stat().st_size,
        }
        done("fcpxml_export", "pronto pro Final Cut")
    except Exception as e:
        warn("fcpxml_export", str(e))
    ctx.check_cancel()

    # ── 11. social copy (cheap, last) ────────────────────────────────
    step("social_copy", "Gerando hook + descrição pro social…", 0.05)
    try:
        story_title = None
        story_logline = None
        if (pdir / "story.json").exists():
            sj = storage.read_json(pid, "story.json")
            story_title = sj.get("title")
            story_logline = sj.get("logline")
        social = await social_svc.generate(
            transcript_text=transcript.get("text") or "",
            title=story_title,
            logline=story_logline,
            brand_name=state.name,
            language=language or "pt",
        )
        storage.write_json(pid, "social_copy.json", social.model_dump())
        out["social_hook"] = social.hook
        done("social_copy", "copy social pronto")
    except Exception as e:
        warn("social_copy", str(e))

    ctx.progress(1.0, "Pronto! Tudo gerado.")
    return out
