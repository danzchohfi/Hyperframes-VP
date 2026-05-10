"""FastAPI app for the HyperFrames Video Pipeline.

Endpoints (all under /api):

  POST   /projects                         create project
  GET    /projects                         list projects
  GET    /projects/{id}                    get project state
  DELETE /projects/{id}                    delete project
  POST   /projects/{id}/upload             upload source video (multipart)
  POST   /projects/{id}/transcribe         run Whisper
  POST   /projects/{id}/cut-silences       detect + plan silence cuts
  POST   /projects/{id}/apply              materialize cut.mp4 (silence cuts) and graded.mp4 (LUT)
  PUT    /projects/{id}/brand              set brand book (json body)
  POST   /projects/{id}/lut                upload .cube LUT
  POST   /projects/{id}/render             generate composition + render
  POST   /projects/{id}/export             reframe to target aspect; returns export id
  GET    /projects/{id}/files/{name}       serve a file from the project dir
  GET    /projects/{id}/exports/{name}     download an export

The web SPA is mounted at /.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import json as _json

from fastapi.responses import StreamingResponse

from . import storage
from .services import ffmpeg as ff
from .services import whisper, silence, composer, render, fcpxml, music
from .services import fillers as fillers_svc
from .services import soundbites as sb_svc
from .services import story as story_svc
from .services import vision as vision_svc
from .services import premiere_xml
from .services import roughcut as rc_svc
from .services import broll_match as broll_svc
from .services import smartcrop
from .services import brand_presets
from .services import bundle as bundle_svc
from .services import speakers as speakers_svc
from .services import highlights as highlights_svc
from .services import social_copy as social_svc
from .services import templates as templates_svc
from .services.events import bus, emit_stage, emit_log, emit_state_changed
from .services import captions as captions_svc
from .services.brand import BrandBook

app = FastAPI(title="HyperFrames Video Pipeline", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- request models ----------------------------------------------------------

class CreateProjectIn(BaseModel):
    name: str = "Untitled"


class CutOptionsIn(BaseModel):
    noise_db: float = -32.0
    min_silence: float = 0.5
    pad: float = 0.08


class ApplyIn(BaseModel):
    loudnorm: bool = False
    denoise: bool = False


class RenderIn(BaseModel):
    aspect: str = "9:16"  # 9:16 | 16:9 | 1:1
    include_captions: bool = True
    source: str = "graded"  # graded | roughcut | source
    include_chapter_cards: bool = False  # show chapter title cards from the story


class ExportIn(BaseModel):
    aspect: str = "9:16"


class FcpxmlIn(BaseModel):
    multicam: bool = False
    primary_angle: int = 0
    include_word_markers: bool = True
    use_cuts: bool = True
    use_roughcut: bool = False
    include_broll: bool = False


class AngleIn(BaseModel):
    name: str = "Angle"


class MusicSearchIn(BaseModel):
    suggestion: dict[str, Any] | None = None
    limit: int = 10


class MusicSelectIn(BaseModel):
    track: dict[str, Any]


class FillersIn(BaseModel):
    language: str | None = "auto"  # auto | pt | en
    custom: list[str] | None = None
    pad: float = 0.04


class SoundbitesIn(BaseModel):
    pass  # no params yet


class StoryIn(BaseModel):
    structure: str = "four_act"  # four_act | hero_journey | explainer | testimonial | before_after
    style_note: str | None = None


class RoughCutIn(BaseModel):
    use_story: bool = True            # build from story chapters
    soundbite_ids: list[str] | None = None  # override: explicit list
    aspect: str | None = None         # if set, also reframes
    apply_lut: bool = True
    loudnorm: bool = False
    denoise: bool = False
    smart_crop: bool = False          # face-aware crop when aspect changes


class PremiereXmlIn(BaseModel):
    use_cuts: bool = True
    use_roughcut: bool = False
    include_broll: bool = False


class FcpxmlMultitrackIn(BaseModel):
    use_cuts: bool = True
    use_roughcut: bool = False
    include_broll: bool = True
    include_word_markers: bool = True


class BrollPlaceIn(BaseModel):
    pass


class SmartReframeIn(BaseModel):
    aspect: str = "9:16"
    use_roughcut: bool = True


class SnapshotIn(BaseModel):
    label: str = ""


class BrandPresetIn(BaseModel):
    name: str
    brand: dict[str, Any]


# ---- helpers -----------------------------------------------------------------

def _stage(
    state: storage.ProjectState,
    name: str,
    status: str,
    msg: str | None = None,
    *,
    progress: float | None = None,
) -> None:
    s = state.stage(name)
    s.status = status
    if msg is not None:
        s.message = msg
    if status == "running":
        s.started_at = storage._now()
    elif status in ("done", "error"):
        s.finished_at = storage._now()
    storage.save(state)
    emit_stage(state.id, name, status, msg, progress=progress)
    emit_state_changed(state.id)


def _load(pid: str) -> storage.ProjectState:
    try:
        return storage.load(pid)
    except FileNotFoundError:
        raise HTTPException(404, f"project {pid} not found")


# ---- project crud ------------------------------------------------------------

@app.post("/api/projects")
async def create_project(body: CreateProjectIn) -> dict[str, Any]:
    state = storage.create(body.name)
    return state.model_dump()


@app.get("/api/projects")
async def list_projects() -> list[dict[str, Any]]:
    return storage.list_projects()


@app.get("/api/projects/{pid}")
async def get_project(pid: str) -> dict[str, Any]:
    state = _load(pid)
    payload = state.model_dump()

    # stale-state hints — recompute from file mtimes
    pdir = storage.project_dir(pid)
    payload["stale"] = _compute_stale(pdir, state)
    payload["render_active"] = render.is_active(pid)
    return payload


def _compute_stale(pdir: Path, state: storage.ProjectState) -> dict[str, bool]:
    def _mtime(name: str) -> float:
        f = pdir / name
        return f.stat().st_mtime if f.exists() else 0.0

    cuts_mt = max(_mtime("cuts.json"), _mtime("fillers.json"))
    sb_mt = _mtime("soundbites.json")
    transcript_mt = _mtime("transcript.json")
    story_mt = _mtime("story.json")
    rc_mt = _mtime("roughcut.mp4")
    graded_mt = _mtime("graded.mp4")

    return {
        "graded_vs_cuts": graded_mt > 0 and cuts_mt > graded_mt,
        "soundbites_vs_transcript": sb_mt > 0 and transcript_mt > sb_mt,
        "story_vs_soundbites": story_mt > 0 and sb_mt > story_mt,
        "roughcut_vs_story": rc_mt > 0 and story_mt > rc_mt,
    }


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str) -> dict[str, str]:
    pdir = storage.project_dir(pid)
    if not pdir.exists():
        raise HTTPException(404, "not found")
    shutil.rmtree(pdir)
    return {"status": "deleted", "id": pid}


# ---- upload ------------------------------------------------------------------

@app.post("/api/projects/{pid}/upload")
async def upload_source(pid: str, file: UploadFile = File(...)) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)

    ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    raw_path = pdir / f"source_raw{ext}"
    norm_path = pdir / "source.mp4"

    _stage(state, "upload", "running", f"writing {file.filename}")
    async with aiofiles.open(raw_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            await out.write(chunk)

    try:
        await ff.normalize(raw_path, norm_path)
        dur = await ff.duration(norm_path)
    except ff.FFmpegError as e:
        _stage(state, "upload", "error", str(e))
        raise HTTPException(400, f"ffmpeg failed: {e}")

    state.source_filename = file.filename
    state.source_duration = dur
    storage.save(state)
    _stage(state, "upload", "done", f"{dur:.2f}s")
    return state.model_dump()


# ---- transcribe --------------------------------------------------------------

class TranscribeIn(BaseModel):
    language: str | None = None


@app.post("/api/projects/{pid}/transcribe")
async def transcribe(pid: str, body: TranscribeIn | None = None) -> dict[str, Any]:
    body = body or TranscribeIn()
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source uploaded")

    _stage(state, "transcribe", "running", f"Whisper · lang={body.language or 'auto'}")
    try:
        result = await whisper.transcribe(src, language=body.language)
    except Exception as e:
        _stage(state, "transcribe", "error", str(e))
        raise HTTPException(502, f"transcription failed: {e}")

    storage.write_json(pid, "transcript.json", result)
    state.has_transcript = True
    storage.save(state)
    _stage(
        state,
        "transcribe",
        "done",
        f"{len(result.get('words') or [])} words, lang={result.get('language')}",
    )
    return {"words": len(result.get("words") or []), "language": result.get("language")}


# ---- silence cuts ------------------------------------------------------------

@app.post("/api/projects/{pid}/cut-silences")
async def cut_silences(pid: str, body: CutOptionsIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source uploaded")

    _stage(state, "silence", "running", f"silencedetect@{body.noise_db}dB d={body.min_silence}s")
    try:
        silences = await ff.detect_silences(src, noise_db=body.noise_db, min_silence=body.min_silence)
        dur = state.source_duration or await ff.duration(src)
        keep = silence.plan_keep_segments(dur, silences, pad=body.pad)
    except Exception as e:
        _stage(state, "silence", "error", str(e))
        raise HTTPException(500, str(e))

    plan = {
        "options": body.model_dump(),
        "source_duration": dur,
        "silences": [{"start": s, "end": e} for s, e in silences],
        "keep": [{"start": s, "end": e} for s, e in keep],
        "kept_duration": silence.total_kept(keep),
    }
    storage.write_json(pid, "cuts.json", plan)
    state.has_cuts = True
    storage.save(state)
    _stage(
        state,
        "silence",
        "done",
        f"{len(silences)} silences → kept {plan['kept_duration']:.2f}s of {dur:.2f}s",
    )
    return plan


# ---- LUT ---------------------------------------------------------------------

@app.post("/api/projects/{pid}/lut")
async def upload_lut(pid: str, file: UploadFile = File(...)) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    name = file.filename or "lut.cube"
    if not name.lower().endswith(".cube"):
        raise HTTPException(400, "expected a .cube file")

    dst = pdir / "lut.cube"
    async with aiofiles.open(dst, "wb") as out:
        while chunk := await file.read(64 * 1024):
            await out.write(chunk)
    state.has_lut = True
    state.lut_filename = name
    storage.save(state)
    _stage(state, "lut_upload", "done", name)
    return {"lut_filename": name, "bytes": dst.stat().st_size}


# ---- brand book --------------------------------------------------------------

@app.put("/api/projects/{pid}/brand")
async def set_brand(pid: str, brand: BrandBook) -> dict[str, Any]:
    state = _load(pid)
    storage.write_json(pid, "brand.json", brand.model_dump())
    state.has_brand = True
    storage.save(state)
    _stage(state, "brand", "done", brand.name)
    return brand.model_dump()


@app.get("/api/projects/{pid}/brand")
async def get_brand(pid: str) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_brand:
        return BrandBook().model_dump()
    return storage.read_json(pid, "brand.json")


# ---- apply (silence-cut + LUT) ----------------------------------------------

@app.post("/api/projects/{pid}/apply")
async def apply_edits(pid: str, body: ApplyIn | None = None) -> dict[str, Any]:
    body = body or ApplyIn()
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source uploaded")

    _stage(state, "apply", "running", "encoding kept segments")
    try:
        keep: list[tuple[float, float]]
        if state.has_cuts:
            plan = storage.read_json(pid, "cuts.json")
            keep = [(seg["start"], seg["end"]) for seg in plan["keep"]]
        else:
            keep = [(0.0, state.source_duration or await ff.duration(src))]

        if state.has_fillers:
            filler_plan = storage.read_json(pid, "fillers.json")
            drop = [(r["start"], r["end"]) for r in filler_plan.get("ranges", [])]
            keep = fillers_svc.subtract_ranges(keep, drop)

        lut_path = pdir / "lut.cube" if state.has_lut else None
        out_path = pdir / "graded.mp4"
        await ff.cut_segments(
            src, out_path, keep,
            lut=lut_path, loudnorm=body.loudnorm, denoise=body.denoise,
        )
        out_dur = await ff.duration(out_path)
    except Exception as e:
        _stage(state, "apply", "error", str(e))
        raise HTTPException(500, str(e))

    detail = f"{out_dur:.2f}s edited · fillers={state.fillers_count}"
    if body.loudnorm:
        detail += " · loudnorm"
    if body.denoise:
        detail += " · denoise"
    _stage(state, "apply", "done", detail)
    return {
        "duration": out_dur,
        "lut_applied": state.has_lut,
        "fillers_removed": state.fillers_count if state.has_fillers else 0,
        "loudnorm": body.loudnorm,
        "denoise": body.denoise,
    }


# ---- render via Hyperframes --------------------------------------------------

@app.post("/api/projects/{pid}/render")
async def do_render(pid: str, body: RenderIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)

    candidates = {
        "roughcut": pdir / "roughcut.mp4",
        "graded": pdir / "graded.mp4",
        "source": pdir / "source.mp4",
    }
    edited = candidates.get(body.source) or candidates["graded"]
    if not edited.exists():
        # fall back through preferences
        for pick in ("roughcut", "graded", "source"):
            if candidates[pick].exists():
                edited = candidates[pick]
                break
    if not edited.exists():
        raise HTTPException(400, "no video to render")

    brand = (
        BrandBook.model_validate(storage.read_json(pid, "brand.json"))
        if state.has_brand
        else BrandBook()
    )
    transcript = (
        storage.read_json(pid, "transcript.json")
        if (state.has_transcript and body.include_captions)
        else None
    )
    if transcript and body.source == "roughcut" and state.has_roughcut:
        rc_plan = storage.read_json(pid, "roughcut.json")
        keep_for_words = [(r["start"], r["end"]) for r in rc_plan["ranges"]]
        transcript = {
            **transcript,
            "words": captions_svc.retime_words(transcript.get("words") or [], keep_for_words),
            "segments": captions_svc.retime_segments(transcript.get("segments") or [], keep_for_words),
        }

    _stage(state, "render", "running", "building composition")
    try:
        dur = await ff.duration(edited)
        chapters_for_render = None
        if body.include_chapter_cards and state.has_story:
            story = storage.read_json(pid, "story.json")
            if state.has_soundbites:
                analysis = storage.read_json(pid, "soundbites.json")
                chapters_for_render = rc_svc.chapter_marker_plan(
                    story["chapters"], analysis["soundbites"],
                )
        comp_dir = composer.build_composition(
            project_dir=pdir,
            video_path=edited,
            video_duration=dur,
            transcript=transcript,
            brand=brand,
            aspect=body.aspect,
            chapters=chapters_for_render,
        )
    except Exception as e:
        _stage(state, "render", "error", f"compose: {e}")
        raise HTTPException(500, str(e))

    try:
        _stage(state, "render", "running", "running hyperframes render")
        def _render_event(ev: dict) -> None:
            pct = ev.get("progress")
            label = ev.get("label", "")
            emit_stage(pid, "render", "running", f"{label} ({pct}%)" if pct is not None else label, progress=float(pct) / 100.0 if pct is not None else None)
        out = await render.render(
            comp_dir,
            output_dir=pdir / "exports",
            name="render",
            on_event=_render_event,
            project_id=pid,
        )
    except Exception as e:
        _stage(state, "render", "error", f"render: {e}")
        raise HTTPException(500, str(e))

    state.has_render = True
    state.last_export = out.name
    storage.save(state)
    _stage(state, "render", "done", out.name)
    return {"export": out.name, "url": f"/api/projects/{pid}/exports/{out.name}"}


# ---- export reframe ----------------------------------------------------------

@app.post("/api/projects/{pid}/export")
async def export(pid: str, body: ExportIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "exports" / "render.mp4"
    if not src.exists():
        raise HTTPException(400, "render first")
    name = f"export-{body.aspect.replace(':', 'x')}.mp4"
    dst = pdir / "exports" / name
    try:
        await ff.to_aspect(src, dst, body.aspect)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"export": name, "url": f"/api/projects/{pid}/exports/{name}"}


# ---- multicam angles ---------------------------------------------------------

@app.post("/api/projects/{pid}/angles")
async def add_angle(pid: str, name: str = Form("Angle"), file: UploadFile = File(...)) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    angles_dir = pdir / "angles"
    angles_dir.mkdir(exist_ok=True)

    idx = len(state.angles) + 1
    ext = Path(file.filename or "angle.mp4").suffix or ".mp4"
    raw = angles_dir / f"angle{idx}_raw{ext}"
    norm = angles_dir / f"angle{idx}.mp4"

    async with aiofiles.open(raw, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            await out.write(chunk)

    try:
        await ff.normalize(raw, norm)
        dur = await ff.duration(norm)
    except ff.FFmpegError as e:
        raise HTTPException(400, f"ffmpeg failed: {e}")

    raw.unlink(missing_ok=True)
    angle_record = {
        "index": idx - 1,
        "name": name or f"Angle {idx}",
        "filename": norm.name,
        "duration": dur,
    }
    state.angles.append(angle_record)
    storage.save(state)
    _stage(state, "angle", "done", f"{angle_record['name']} ({dur:.2f}s)")
    return angle_record


@app.delete("/api/projects/{pid}/angles/{idx}")
async def delete_angle(pid: str, idx: int) -> dict[str, str]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    if idx < 0 or idx >= len(state.angles):
        raise HTTPException(404, "angle not found")
    record = state.angles.pop(idx)
    f = pdir / "angles" / record["filename"]
    f.unlink(missing_ok=True)
    # re-index remaining angles in metadata only
    for i, a in enumerate(state.angles):
        a["index"] = i
    storage.save(state)
    return {"status": "deleted"}


# ---- FCPXML export -----------------------------------------------------------

@app.post("/api/projects/{pid}/export/fcpxml")
async def export_fcpxml(pid: str, body: FcpxmlIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    source = pdir / "source.mp4"
    if not source.exists():
        raise HTTPException(400, "no source video")

    keep: list[tuple[float, float]] | None = None
    if body.use_roughcut and state.has_roughcut:
        rc = storage.read_json(pid, "roughcut.json")
        keep = [(seg["start"], seg["end"]) for seg in rc["ranges"]]
    elif body.use_cuts and state.has_cuts:
        plan = storage.read_json(pid, "cuts.json")
        keep = [(seg["start"], seg["end"]) for seg in plan["keep"]]

    transcript = (
        storage.read_json(pid, "transcript.json")
        if (state.has_transcript and body.include_word_markers)
        else None
    )

    broll_angles_paths: list[tuple[str, Path]] = []
    broll_placements: list[dict] = []
    if body.include_broll and state.angles:
        angles_dir = pdir / "angles"
        broll_angles_paths = [(a["name"], angles_dir / a["filename"]) for a in state.angles]
        plac_path = pdir / "broll_placement.json"
        if plac_path.exists():
            broll_placements = storage.read_json(pid, "broll_placement.json").get("placements", [])

    try:
        if body.include_broll and broll_placements:
            xml = await fcpxml.build_multitrack_fcpxml(
                project_name=state.name,
                source=source,
                cuts=keep,
                transcript=transcript,
                broll_angles=broll_angles_paths,
                broll_placements=broll_placements,
            )
            kind = "multitrack"
        elif body.multicam and state.angles:
            angles_dir = pdir / "angles"
            angle_paths = [(a["name"], angles_dir / a["filename"]) for a in state.angles]
            primary = max(0, min(body.primary_angle, len(angle_paths) - 1))
            xml = await fcpxml.build_multicam_fcpxml(
                project_name=state.name,
                angles=angle_paths,
                primary_index=primary,
                cuts=keep,
                transcript=transcript,
            )
            kind = "multicam"
        else:
            xml = await fcpxml.build_single_cam_fcpxml(
                project_name=state.name,
                source=source,
                cuts=keep,
                transcript=transcript,
            )
            kind = "singlecam"
    except Exception as e:
        _stage(state, "fcpxml", "error", str(e))
        raise HTTPException(500, str(e))

    out_path = pdir / "exports" / f"{state.name.replace(' ', '_')}.fcpxml"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(xml, encoding="utf-8")
    _stage(state, "fcpxml", "done", f"{kind} · {out_path.name}")
    return {
        "export": out_path.name,
        "kind": kind,
        "url": f"/api/projects/{pid}/exports/{out_path.name}",
        "bytes": out_path.stat().st_size,
    }


# ---- music suggestion --------------------------------------------------------

@app.post("/api/projects/{pid}/music/suggest")
async def music_suggest(pid: str) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(400, "transcribe first")
    transcript = storage.read_json(pid, "transcript.json")
    text = (transcript.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "transcript is empty")

    _stage(state, "music_suggest", "running", "asking the model")
    try:
        suggestion = await music.suggest_music(text, duration=state.source_duration or 0.0)
    except Exception as e:
        _stage(state, "music_suggest", "error", str(e))
        raise HTTPException(502, f"suggestion failed: {e}")

    state.music_suggestion = suggestion.model_dump()
    storage.save(state)
    _stage(state, "music_suggest", "done", suggestion.description[:80])
    return suggestion.model_dump()


@app.post("/api/projects/{pid}/music/search")
async def music_search(pid: str, body: MusicSearchIn) -> dict[str, Any]:
    state = _load(pid)
    suggestion_raw = body.suggestion or state.music_suggestion
    if not suggestion_raw:
        raise HTTPException(400, "run /music/suggest first or pass `suggestion` in body")
    suggestion = music.MusicSuggestion.model_validate(suggestion_raw)

    provider = music.get_provider()
    if provider is None:
        return {
            "provider": None,
            "tracks": [],
            "epidemic_search_url": suggestion.epidemic_search_url,
            "hint": "Set EPIDEMIC_SOUND_TOKEN in .env to enable in-app search.",
        }

    try:
        tracks = await provider.search(suggestion, limit=body.limit)
    except Exception as e:
        raise HTTPException(502, f"search failed: {e}")

    return {
        "provider": provider.name,
        "tracks": [t.model_dump() for t in tracks],
        "epidemic_search_url": suggestion.epidemic_search_url,
    }


@app.post("/api/projects/{pid}/music/select")
async def music_select(pid: str, body: MusicSelectIn) -> dict[str, Any]:
    state = _load(pid)
    state.music_track = body.track
    storage.save(state)
    _stage(state, "music_select", "done", body.track.get("title", "track"))
    return {"track": body.track}


# ---- filler-word removal -----------------------------------------------------

@app.post("/api/projects/{pid}/cut-fillers")
async def cut_fillers(pid: str, body: FillersIn) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(400, "transcribe first")
    transcript = storage.read_json(pid, "transcript.json")
    words = transcript.get("words") or []
    if not words:
        raise HTTPException(400, "transcript has no word timings")

    lang = body.language if body.language and body.language != "auto" else (transcript.get("language") or "auto")
    ranges = fillers_svc.detect_filler_ranges(words, language=lang, custom=body.custom, pad=body.pad)
    storage.write_json(pid, "fillers.json", {
        "language": lang,
        "ranges": [{"start": s, "end": e} for s, e in ranges],
        "stats": fillers_svc.stats(ranges),
    })
    state.has_fillers = True
    state.fillers_count = len(ranges)
    storage.save(state)
    _stage(state, "fillers", "done", f"{len(ranges)} ranges · {fillers_svc.stats(ranges)['duration']:.2f}s")
    return {"count": len(ranges), "duration": fillers_svc.stats(ranges)["duration"]}


# ---- soundbites + topics -----------------------------------------------------

@app.post("/api/projects/{pid}/soundbites")
async def soundbites(pid: str, _body: SoundbitesIn | None = None) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(400, "transcribe first")
    transcript = storage.read_json(pid, "transcript.json")

    _stage(state, "soundbites", "running", "asking the model")
    try:
        analysis = await sb_svc.analyze(transcript)
    except Exception as e:
        _stage(state, "soundbites", "error", str(e))
        raise HTTPException(502, str(e))

    storage.write_json(pid, "soundbites.json", analysis.model_dump())
    state.has_soundbites = True
    storage.save(state)
    _stage(
        state,
        "soundbites",
        "done",
        f"{len(analysis.soundbites)} bites · {len(analysis.topics)} topics",
    )
    return analysis.model_dump()


@app.get("/api/projects/{pid}/soundbites")
async def get_soundbites(pid: str):
    state = _load(pid)
    if not state.has_soundbites:
        raise HTTPException(404, "no soundbites")
    return JSONResponse(storage.read_json(pid, "soundbites.json"))


# ---- story framework ---------------------------------------------------------

@app.post("/api/projects/{pid}/story")
async def build_story(pid: str, body: StoryIn) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_soundbites:
        raise HTTPException(400, "extract soundbites first")
    analysis = storage.read_json(pid, "soundbites.json")

    _stage(state, "story", "running", body.structure)
    try:
        story = await story_svc.propose(
            analysis["soundbites"], analysis["topics"],
            structure=body.structure, style_note=body.style_note,
        )
    except Exception as e:
        _stage(state, "story", "error", str(e))
        raise HTTPException(502, str(e))

    storage.write_json(pid, "story.json", story.model_dump())
    state.has_story = True
    storage.save(state)
    _stage(state, "story", "done", f"{len(story.chapters)} chapters · {story.title}")
    return story.model_dump()


@app.get("/api/projects/{pid}/story")
async def get_story(pid: str):
    state = _load(pid)
    if not state.has_story:
        raise HTTPException(404, "no story")
    return JSONResponse(storage.read_json(pid, "story.json"))


# ---- B-roll vision tagging ---------------------------------------------------

@app.post("/api/projects/{pid}/angles/{idx}/tag")
async def tag_angle(pid: str, idx: int) -> dict[str, Any]:
    state = _load(pid)
    if idx < 0 or idx >= len(state.angles):
        raise HTTPException(404, "angle not found")
    pdir = storage.project_dir(pid)
    angle = state.angles[idx]
    src = pdir / "angles" / angle["filename"]
    if not src.exists():
        raise HTTPException(404, "angle file missing")

    _stage(state, "vision_tag", "running", angle["name"])
    try:
        tags = await vision_svc.tag_clip(src)
    except Exception as e:
        _stage(state, "vision_tag", "error", str(e))
        raise HTTPException(502, str(e))

    angle["tags"] = tags.model_dump()
    storage.save(state)
    _stage(state, "vision_tag", "done", tags.summary or angle["name"])
    return angle


@app.post("/api/projects/{pid}/source/tag")
async def tag_source(pid: str) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source")
    _stage(state, "vision_tag", "running", "source.mp4")
    try:
        tags = await vision_svc.tag_clip(src)
    except Exception as e:
        _stage(state, "vision_tag", "error", str(e))
        raise HTTPException(502, str(e))
    storage.write_json(pid, "source_tags.json", tags.model_dump())
    _stage(state, "vision_tag", "done", tags.summary or "source")
    return tags.model_dump()


# ---- rough-cut assembly ------------------------------------------------------

@app.post("/api/projects/{pid}/roughcut")
async def roughcut(pid: str, body: RoughCutIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source uploaded")
    if not state.has_soundbites:
        raise HTTPException(400, "extract soundbites first")

    analysis = storage.read_json(pid, "soundbites.json")

    if body.use_story:
        if not state.has_story:
            raise HTTPException(400, "build story first or set use_story=false with explicit soundbite_ids")
        story = storage.read_json(pid, "story.json")
        ranges = rc_svc.chapter_ranges(story["chapters"], analysis["soundbites"])
        chapter_markers = rc_svc.chapter_marker_plan(story["chapters"], analysis["soundbites"])
    else:
        if not body.soundbite_ids:
            raise HTTPException(400, "soundbite_ids required when use_story=false")
        ranges = rc_svc.selected_ranges(body.soundbite_ids, analysis["soundbites"])
        chapter_markers = []

    if not ranges:
        raise HTTPException(400, "no ranges resolved")

    lut = pdir / "lut.cube" if (state.has_lut and body.apply_lut) else None
    out_path = pdir / "roughcut.mp4"

    _stage(state, "roughcut", "running", f"{len(ranges)} segments")
    try:
        await ff.cut_segments(
            src, out_path, ranges,
            lut=lut, loudnorm=body.loudnorm, denoise=body.denoise,
        )
        out_dur = await ff.duration(out_path)
    except Exception as e:
        _stage(state, "roughcut", "error", str(e))
        raise HTTPException(500, str(e))

    storage.write_json(pid, "roughcut.json", {
        "ranges": [{"start": s, "end": e} for s, e in ranges],
        "duration": out_dur,
        "chapter_markers": chapter_markers,
    })
    state.has_roughcut = True
    storage.save(state)
    _stage(state, "roughcut", "done", f"{out_dur:.2f}s · {len(chapter_markers)} chapters")

    result: dict[str, Any] = {
        "duration": out_dur,
        "segments": len(ranges),
        "chapters": len(chapter_markers),
        "url": f"/api/projects/{pid}/files/roughcut.mp4",
    }
    if body.aspect:
        try:
            reframed = pdir / "exports" / f"roughcut-{body.aspect.replace(':', 'x')}.mp4"
            reframed.parent.mkdir(exist_ok=True)
            await ff.to_aspect(out_path, reframed, body.aspect)
            result["reframed_url"] = f"/api/projects/{pid}/exports/{reframed.name}"
        except Exception as e:
            result["reframe_error"] = str(e)

    return result


# ---- highlights reel --------------------------------------------------------

class HighlightsIn(BaseModel):
    target_seconds: float = 30.0
    apply_lut: bool = True
    aspect: str | None = None


@app.post("/api/projects/{pid}/highlights")
async def highlights(pid: str, body: HighlightsIn) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_soundbites:
        raise HTTPException(400, "extract soundbites first")
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source")
    analysis = storage.read_json(pid, "soundbites.json")
    ranges = highlights_svc.select(analysis["soundbites"], target_seconds=body.target_seconds)
    if not ranges:
        raise HTTPException(400, "no usable soundbites")
    lut = pdir / "lut.cube" if (state.has_lut and body.apply_lut) else None
    out = pdir / "highlights.mp4"
    _stage(state, "highlights", "running", f"{len(ranges)} segments")
    try:
        await ff.cut_segments(src, out, ranges, lut=lut)
        dur = await ff.duration(out)
    except Exception as e:
        _stage(state, "highlights", "error", str(e))
        raise HTTPException(500, str(e))
    storage.write_json(pid, "highlights.json", {
        "target_seconds": body.target_seconds,
        "ranges": [{"start": s, "end": e} for s, e in ranges],
        "duration": dur,
    })
    _stage(state, "highlights", "done", f"{dur:.1f}s · {len(ranges)} bites")

    result: dict[str, Any] = {
        "duration": dur,
        "segments": len(ranges),
        "url": f"/api/projects/{pid}/files/highlights.mp4",
    }
    if body.aspect:
        out_re = pdir / "exports" / f"highlights-{body.aspect.replace(':', 'x')}.mp4"
        out_re.parent.mkdir(exist_ok=True)
        try:
            await ff.to_aspect(out, out_re, body.aspect)
            result["reframed_url"] = f"/api/projects/{pid}/exports/{out_re.name}"
        except Exception as e:
            result["reframe_error"] = str(e)
    return result


# ---- social-media copy ------------------------------------------------------

class SocialCopyIn(BaseModel):
    language: str = "pt"


@app.post("/api/projects/{pid}/social-copy")
async def social_copy(pid: str, body: SocialCopyIn) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(400, "transcribe first")
    transcript = storage.read_json(pid, "transcript.json")
    title = None
    logline = None
    if state.has_story:
        story = storage.read_json(pid, "story.json")
        title = story.get("title")
        logline = story.get("logline")
    brand_name = state.name
    if state.has_brand:
        try:
            brand_name = storage.read_json(pid, "brand.json").get("name") or state.name
        except Exception:
            pass

    _stage(state, "social_copy", "running", body.language)
    try:
        copy = await social_svc.generate(
            transcript_text=transcript.get("text") or "",
            title=title,
            logline=logline,
            brand_name=brand_name,
            language=body.language,
        )
    except Exception as e:
        _stage(state, "social_copy", "error", str(e))
        raise HTTPException(502, str(e))
    storage.write_json(pid, "social_copy.json", copy.model_dump())
    _stage(state, "social_copy", "done", copy.hook[:60] or "ok")
    return copy.model_dump()


# ---- audio-only export ------------------------------------------------------

class AudioExportIn(BaseModel):
    format: str = "mp3"   # mp3 | wav | m4a
    source: str = "graded"  # graded | roughcut | source | highlights


@app.post("/api/projects/{pid}/export/audio")
async def export_audio(pid: str, body: AudioExportIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    candidates = {
        "graded": pdir / "graded.mp4",
        "roughcut": pdir / "roughcut.mp4",
        "source": pdir / "source.mp4",
        "highlights": pdir / "highlights.mp4",
    }
    src = candidates.get(body.source)
    if not src or not src.exists():
        raise HTTPException(400, f"{body.source} not available")
    if body.format not in ("mp3", "wav", "m4a"):
        raise HTTPException(400, "format must be mp3, wav or m4a")

    name = f"{state.name.replace(' ', '_')}-{body.source}.{body.format}"
    out = pdir / "exports" / name
    out.parent.mkdir(exist_ok=True)
    _stage(state, "audio_export", "running", body.format)
    try:
        await ff.extract_audio(src, out, format=body.format)
    except Exception as e:
        _stage(state, "audio_export", "error", str(e))
        raise HTTPException(500, str(e))
    _stage(state, "audio_export", "done", f"{out.name} · {out.stat().st_size // 1024} KB")
    return {
        "export": out.name,
        "url": f"/api/projects/{pid}/exports/{out.name}",
        "bytes": out.stat().st_size,
    }


# ---- cancel running render --------------------------------------------------

@app.post("/api/projects/{pid}/render/cancel")
async def cancel_render(pid: str) -> dict[str, bool]:
    cancelled = await render.cancel(pid)
    if cancelled:
        state = _load(pid)
        _stage(state, "render", "error", "cancelled")
    return {"cancelled": cancelled}


@app.get("/api/projects/{pid}/render/status")
async def render_status(pid: str) -> dict[str, bool]:
    return {"running": render.is_active(pid)}


# ---- Hyperframes preset templates -------------------------------------------

@app.get("/api/templates")
async def list_templates() -> list[dict[str, Any]]:
    return templates_svc.list_templates()


class ApplyTemplateIn(BaseModel):
    template_id: str


@app.post("/api/projects/{pid}/template")
async def apply_template(pid: str, body: ApplyTemplateIn) -> dict[str, Any]:
    state = _load(pid)
    try:
        tpl = templates_svc.get(body.template_id)
    except KeyError:
        raise HTTPException(404, "template not found")
    brand = BrandBook.model_validate(tpl["brand"])
    storage.write_json(pid, "brand.json", brand.model_dump())
    state.has_brand = True
    storage.save(state)
    _stage(state, "template", "done", tpl["label"])
    return {
        "applied": body.template_id,
        "label": tpl["label"],
        "aspect": tpl["aspect"],
        "render_source": tpl["render_source"],
        "include_chapter_cards": tpl["include_chapter_cards"],
        "brand": brand.model_dump(),
    }


# ---- speaker-turn hints -----------------------------------------------------

@app.post("/api/projects/{pid}/speakers")
async def detect_speakers(pid: str, gap: float = 1.2) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(400, "transcribe first")
    transcript = storage.read_json(pid, "transcript.json")
    turns = speakers_svc.segment_turns(transcript.get("words") or [], gap_threshold=gap)
    storage.write_json(pid, "speakers.json", {"gap": gap, "turns": turns})
    _stage(state, "speakers", "done", f"{len(turns)} turns")
    return {"turns": turns}


# ---- chapter thumbnails -----------------------------------------------------

@app.post("/api/projects/{pid}/chapter-thumbs")
async def chapter_thumbs(pid: str) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_story:
        raise HTTPException(400, "build story first")
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source")
    story = storage.read_json(pid, "story.json")
    analysis = storage.read_json(pid, "soundbites.json") if state.has_soundbites else {"soundbites": []}
    bite_by_id = {b["id"]: b for b in analysis.get("soundbites") or []}

    out_dir = pdir / "thumbs"
    out_dir.mkdir(exist_ok=True)
    thumbs: list[dict[str, Any]] = []
    for i, ch in enumerate(story.get("chapters") or []):
        sids = ch.get("soundbite_ids") or []
        if not sids:
            continue
        first = bite_by_id.get(sids[0])
        if not first:
            continue
        at = max(0.05, float(first["start"]) + 0.4)
        out = out_dir / f"chapter_{i + 1:02d}.jpg"
        try:
            await ff.grab_thumbnail(src, out, at=at, width=720)
        except Exception:
            continue
        thumbs.append({
            "chapter_id": ch.get("id"),
            "name": ch.get("name"),
            "url": f"/api/projects/{pid}/files/thumbs/{out.name}",
            "at": at,
        })
    _stage(state, "chapter_thumbs", "done", f"{len(thumbs)} thumbs")
    return {"thumbs": thumbs}


# ---- bundle export ----------------------------------------------------------

@app.post("/api/projects/{pid}/export/bundle")
async def export_bundle(pid: str, include_source: bool = True) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    out = pdir / "exports" / f"{state.name.replace(' ', '_')}-bundle.zip"
    out.parent.mkdir(exist_ok=True)
    n = bundle_svc.build_bundle(pdir, out, include_source=include_source)
    _stage(state, "bundle", "done", f"{n} files · {out.stat().st_size // 1024} KB")
    return {
        "export": out.name,
        "url": f"/api/projects/{pid}/exports/{out.name}",
        "files": n,
        "bytes": out.stat().st_size,
    }


# ---- duplicate project ------------------------------------------------------

@app.post("/api/projects/{pid}/duplicate")
async def duplicate_project(pid: str) -> dict[str, Any]:
    state = _load(pid)
    new = storage.create(f"{state.name} (cópia)")
    src_dir = storage.project_dir(pid)
    dst_dir = storage.project_dir(new.id)

    # copy plan/text artifacts + media
    import shutil
    for name in (
        "transcript.json", "cuts.json", "fillers.json", "soundbites.json",
        "story.json", "roughcut.json", "brand.json", "broll_placement.json",
        "speakers.json", "source.mp4", "graded.mp4", "roughcut.mp4",
        "lut.cube",
    ):
        sf = src_dir / name
        if sf.exists():
            shutil.copy2(sf, dst_dir / name)
    angles_src = src_dir / "angles"
    if angles_src.exists():
        shutil.copytree(angles_src, dst_dir / "angles", dirs_exist_ok=True)

    # carry over flags
    new.source_filename = state.source_filename
    new.source_duration = state.source_duration
    new.has_transcript = state.has_transcript
    new.has_cuts = state.has_cuts
    new.has_lut = state.has_lut
    new.lut_filename = state.lut_filename
    new.has_brand = state.has_brand
    new.angles = list(state.angles)
    new.has_fillers = state.has_fillers
    new.fillers_count = state.fillers_count
    new.has_soundbites = state.has_soundbites
    new.has_story = state.has_story
    new.has_roughcut = state.has_roughcut
    storage.save(new)
    _stage(new, "duplicate", "done", f"from {state.name}")
    return new.model_dump()


# ---- B-roll contextual placement --------------------------------------------

@app.post("/api/projects/{pid}/place-broll")
async def place_broll(pid: str, _body: BrollPlaceIn | None = None) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_soundbites:
        raise HTTPException(400, "extract soundbites first")
    if not state.angles:
        raise HTTPException(400, "no angles")
    if not any(a.get("tags") for a in state.angles):
        raise HTTPException(400, "tag your angles first (POST /angles/{i}/tag)")

    analysis = storage.read_json(pid, "soundbites.json")

    _stage(state, "place_broll", "running", "matching B-roll")
    try:
        plan = await broll_svc.match(analysis["soundbites"], state.angles)
    except Exception as e:
        _stage(state, "place_broll", "error", str(e))
        raise HTTPException(502, str(e))

    # If we have a roughcut, project placements onto the roughcut timeline.
    if state.has_roughcut:
        rc_plan = storage.read_json(pid, "roughcut.json")
        keep = [(r["start"], r["end"]) for r in rc_plan["ranges"]]
        plan = broll_svc.overlay_plan(plan, analysis["soundbites"], keep)

    storage.write_json(pid, "broll_placement.json", plan.model_dump())
    _stage(state, "place_broll", "done", f"{len(plan.placements)} inserts")
    return plan.model_dump()


# ---- Smart reframe (face/subject-aware crop) --------------------------------

@app.post("/api/projects/{pid}/smart-reframe")
async def smart_reframe(pid: str, body: SmartReframeIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / ("roughcut.mp4" if body.use_roughcut and state.has_roughcut else "graded.mp4")
    if not src.exists():
        src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no video")

    _stage(state, "smart_reframe", "running", "detecting subject")
    try:
        anchor = await smartcrop.detect_anchor_x(src)
        out = pdir / "exports" / f"smart-{body.aspect.replace(':', 'x')}.mp4"
        out.parent.mkdir(exist_ok=True)
        await ff.to_aspect_smart(src, out, body.aspect, anchor_x=anchor)
        out_dur = await ff.duration(out)
    except Exception as e:
        _stage(state, "smart_reframe", "error", str(e))
        raise HTTPException(500, str(e))

    _stage(state, "smart_reframe", "done", f"anchor={anchor:.2f} · {out.name}")
    return {
        "aspect": body.aspect,
        "anchor_x": anchor,
        "url": f"/api/projects/{pid}/exports/{out.name}",
        "duration": out_dur,
    }


# ---- snapshots --------------------------------------------------------------

@app.get("/api/projects/{pid}/snapshots")
async def list_snapshots_endpoint(pid: str) -> list[dict[str, Any]]:
    _load(pid)
    return storage.list_snapshots(pid)


@app.post("/api/projects/{pid}/snapshots")
async def take_snapshot_endpoint(pid: str, body: SnapshotIn) -> dict[str, Any]:
    state = _load(pid)
    snap = storage.take_snapshot(pid, body.label or storage._now())
    _stage(state, "snapshot", "done", snap["label"])
    return snap


@app.post("/api/projects/{pid}/snapshots/{snap_id}/restore")
async def restore_snapshot_endpoint(pid: str, snap_id: str) -> dict[str, Any]:
    _load(pid)
    try:
        result = storage.restore_snapshot(pid, snap_id)
    except FileNotFoundError:
        raise HTTPException(404, "snapshot not found")
    state = _load(pid)
    _stage(state, "snapshot", "done", f"restored {snap_id}")
    return result


# ---- brand presets ----------------------------------------------------------

@app.get("/api/brand-presets")
async def list_brand_presets() -> list[dict[str, Any]]:
    return brand_presets.list_presets()


@app.post("/api/brand-presets")
async def save_brand_preset(body: BrandPresetIn) -> dict[str, Any]:
    return brand_presets.save_preset(body.name, body.brand)


@app.delete("/api/brand-presets/{preset_id}")
async def delete_brand_preset(preset_id: str) -> dict[str, bool]:
    return {"deleted": brand_presets.delete_preset(preset_id)}


@app.post("/api/projects/{pid}/brand/from-preset/{preset_id}")
async def apply_brand_preset(pid: str, preset_id: str) -> dict[str, Any]:
    state = _load(pid)
    try:
        preset = brand_presets.get_preset(preset_id)
    except FileNotFoundError:
        raise HTTPException(404, "preset not found")
    preset.pop("_id", None)
    brand = BrandBook.model_validate(preset)
    storage.write_json(pid, "brand.json", brand.model_dump())
    state.has_brand = True
    storage.save(state)
    _stage(state, "brand", "done", f"preset: {brand.name}")
    return brand.model_dump()


# ---- SRT / VTT export -------------------------------------------------------

@app.post("/api/projects/{pid}/export/captions")
async def export_captions(
    pid: str,
    fmt: str = "srt",
    use_roughcut: bool = False,
    style: str = "minimal",
) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(400, "transcribe first")
    if fmt not in ("srt", "vtt", "ass"):
        raise HTTPException(400, "fmt must be srt | vtt | ass")

    transcript = storage.read_json(pid, "transcript.json")
    segments = transcript.get("segments") or []
    words = transcript.get("words") or []

    if use_roughcut:
        if not state.has_roughcut:
            raise HTTPException(400, "no roughcut")
        plan = storage.read_json(pid, "roughcut.json")
        ranges = [(r["start"], r["end"]) for r in plan["ranges"]]
        segments = captions_svc.retime_segments(segments, ranges)
        words = captions_svc.retime_words(words, ranges)

    if fmt == "srt":
        body = captions_svc.render_srt(segments)
    elif fmt == "vtt":
        body = captions_svc.render_vtt(segments)
    else:  # ass
        # attach words to each segment
        if words and segments:
            segs_with_words: list[dict] = []
            for seg in segments:
                ss = float(seg.get("start") or 0.0)
                se = float(seg.get("end") or ss)
                seg_words = [
                    {"word": w["word"], "start": float(w["start"]), "end": float(w["end"])}
                    for w in words
                    if float(w.get("start", 0.0)) >= ss and float(w.get("end", 0.0)) <= se + 0.05
                ]
                segs_with_words.append({**seg, "words": seg_words})
            segments = segs_with_words
        body = captions_svc.render_ass(segments, style=style)

    pdir = storage.project_dir(pid)
    out = pdir / "exports" / f"{state.name.replace(' ', '_')}.{fmt}"
    out.parent.mkdir(exist_ok=True)
    out.write_text(body, encoding="utf-8")
    _stage(state, "captions", "done", f"{out.name} · {len(segments)} cues · {style}")
    return {
        "export": out.name,
        "url": f"/api/projects/{pid}/exports/{out.name}",
        "cues": len(segments),
        "fmt": fmt,
    }


class BurnIn(BaseModel):
    source: str = "graded"   # graded | roughcut | source | highlights
    style: str = "tiktok"


@app.post("/api/projects/{pid}/export/burn-captions")
async def burn_captions(pid: str, body: BurnIn) -> dict[str, Any]:
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(400, "transcribe first")
    pdir = storage.project_dir(pid)

    candidates = {
        "graded": pdir / "graded.mp4",
        "roughcut": pdir / "roughcut.mp4",
        "source": pdir / "source.mp4",
        "highlights": pdir / "highlights.mp4",
    }
    src = candidates.get(body.source)
    if not src or not src.exists():
        raise HTTPException(400, f"{body.source} not available")

    transcript = storage.read_json(pid, "transcript.json")
    segments = transcript.get("segments") or []
    words = transcript.get("words") or []

    use_roughcut = body.source == "roughcut" and state.has_roughcut
    if use_roughcut:
        rc = storage.read_json(pid, "roughcut.json")
        ranges = [(r["start"], r["end"]) for r in rc["ranges"]]
        segments = captions_svc.retime_segments(segments, ranges)
        words = captions_svc.retime_words(words, ranges)
    elif body.source == "graded" and state.has_cuts:
        plan = storage.read_json(pid, "cuts.json")
        ranges = [(r["start"], r["end"]) for r in plan["keep"]]
        segments = captions_svc.retime_segments(segments, ranges)
        words = captions_svc.retime_words(words, ranges)
    elif body.source == "highlights":
        h = pdir / "highlights.json"
        if h.exists():
            ranges = [(r["start"], r["end"]) for r in storage.read_json(pid, "highlights.json")["ranges"]]
            segments = captions_svc.retime_segments(segments, ranges)
            words = captions_svc.retime_words(words, ranges)

    # attach words
    if words and segments:
        segs_with_words = []
        for seg in segments:
            ss = float(seg.get("start") or 0.0)
            se = float(seg.get("end") or ss)
            seg_words = [
                {"word": w["word"], "start": float(w["start"]), "end": float(w["end"])}
                for w in words
                if float(w.get("start", 0.0)) >= ss and float(w.get("end", 0.0)) <= se + 0.05
            ]
            segs_with_words.append({**seg, "words": seg_words})
        segments = segs_with_words

    ass_text = captions_svc.render_ass(segments, style=body.style)
    ass_path = pdir / "captions.ass"
    ass_path.write_text(ass_text, encoding="utf-8")

    out = pdir / "exports" / f"{state.name.replace(' ', '_')}-burned-{body.source}.mp4"
    out.parent.mkdir(exist_ok=True)
    _stage(state, "burn_captions", "running", f"{body.source} · {body.style}")
    try:
        await ff.burn_subtitles(src, out, ass_path)
    except Exception as e:
        _stage(state, "burn_captions", "error", str(e))
        raise HTTPException(500, str(e))
    _stage(state, "burn_captions", "done", out.name)
    return {
        "export": out.name,
        "url": f"/api/projects/{pid}/exports/{out.name}",
        "bytes": out.stat().st_size,
    }


# ---- Premiere/Resolve XML export --------------------------------------------

@app.post("/api/projects/{pid}/export/premiere")
async def export_premiere(pid: str, body: PremiereXmlIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source")

    if body.use_roughcut:
        if not state.has_roughcut:
            raise HTTPException(400, "no roughcut")
        plan = storage.read_json(pid, "roughcut.json")
        keep = [(seg["start"], seg["end"]) for seg in plan["ranges"]]
    elif body.use_cuts and state.has_cuts:
        plan = storage.read_json(pid, "cuts.json")
        keep = [(seg["start"], seg["end"]) for seg in plan["keep"]]
    else:
        keep = None

    broll_angles_paths: list[tuple[Path, Path]] = []
    broll_placements: list[dict] = []
    if body.include_broll and state.angles:
        angles_dir = pdir / "angles"
        broll_angles_paths = [(a["name"], angles_dir / a["filename"]) for a in state.angles]
        plac_path = pdir / "broll_placement.json"
        if plac_path.exists():
            broll_placements = storage.read_json(pid, "broll_placement.json").get("placements", [])

    try:
        xml = await premiere_xml.build_xmeml(
            project_name=state.name,
            source=src,
            cuts=keep,
            broll_angles=broll_angles_paths,
            broll_placements=broll_placements,
        )
    except Exception as e:
        _stage(state, "premiere_xml", "error", str(e))
        raise HTTPException(500, str(e))

    out = pdir / "exports" / f"{state.name.replace(' ', '_')}.xml"
    out.parent.mkdir(exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    _stage(state, "premiere_xml", "done", out.name)
    return {
        "export": out.name,
        "url": f"/api/projects/{pid}/exports/{out.name}",
        "bytes": out.stat().st_size,
    }


# ---- file serving ------------------------------------------------------------

@app.get("/api/projects/{pid}/files/{name}")
async def serve_file(pid: str, name: str):
    pdir = storage.project_dir(pid)
    fp = (pdir / name).resolve()
    if not fp.exists() or pdir.resolve() not in fp.parents:
        raise HTTPException(404)
    return FileResponse(fp)


@app.get("/api/projects/{pid}/files/thumbs/{name}")
async def serve_thumb(pid: str, name: str):
    pdir = storage.project_dir(pid)
    fp = (pdir / "thumbs" / name).resolve()
    if not fp.exists() or (pdir / "thumbs").resolve() not in fp.parents:
        raise HTTPException(404)
    return FileResponse(fp)


@app.get("/api/projects/{pid}/exports/{name}")
async def serve_export(pid: str, name: str):
    pdir = storage.project_dir(pid)
    fp = (pdir / "exports" / name).resolve()
    if not fp.exists() or (pdir / "exports").resolve() not in fp.parents:
        raise HTTPException(404)
    return FileResponse(fp, media_type="video/mp4", filename=name)


@app.get("/api/projects/{pid}/transcript")
async def get_transcript(pid: str):
    state = _load(pid)
    if not state.has_transcript:
        raise HTTPException(404, "no transcript")
    return JSONResponse(storage.read_json(pid, "transcript.json"))


class CutsFromWordsIn(BaseModel):
    """Time ranges to keep, derived from the user's selection in the transcript."""
    keep: list[dict[str, float]]   # [{"start": 0.0, "end": 1.5}, ...]
    pad: float = 0.05


@app.post("/api/projects/{pid}/cut-from-words")
async def cut_from_words(pid: str, body: CutsFromWordsIn) -> dict[str, Any]:
    state = _load(pid)
    if not state.source_duration:
        raise HTTPException(400, "no source duration; upload first")

    # normalize, sort, merge
    ranges: list[tuple[float, float]] = []
    for r in body.keep:
        s = max(0.0, float(r.get("start", 0.0)) - body.pad)
        e = min(state.source_duration, float(r.get("end", 0.0)) + body.pad)
        if e > s:
            ranges.append((s, e))
    ranges.sort()
    merged: list[tuple[float, float]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1] + 0.01:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    if not merged:
        raise HTTPException(400, "no ranges to keep")

    plan = {
        "options": {"manual": True, "pad": body.pad},
        "source_duration": state.source_duration,
        "silences": [],
        "keep": [{"start": s, "end": e} for s, e in merged],
        "kept_duration": sum(e - s for s, e in merged),
    }
    storage.write_json(pid, "cuts.json", plan)
    state.has_cuts = True
    storage.save(state)
    _stage(state, "silence", "done", f"manual cut · kept {plan['kept_duration']:.2f}s")
    return plan


@app.get("/api/projects/{pid}/cuts")
async def get_cuts(pid: str):
    state = _load(pid)
    if not state.has_cuts:
        raise HTTPException(404, "no cuts plan")
    return JSONResponse(storage.read_json(pid, "cuts.json"))


@app.get("/api/projects/{pid}/events")
async def project_events(pid: str):
    """SSE stream of stage/log/state events for a project."""
    _load(pid)  # 404 if not found

    async def gen():
        q = bus.subscribe(pid)
        try:
            yield f"data: {_json.dumps({'type': 'hello', 'pid': pid})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {_json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(pid, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/search")
async def search_soundbites(q: str = "", limit: int = 25) -> list[dict[str, Any]]:
    """Search soundbites across all projects."""
    if not q.strip():
        return []
    needle = q.strip().lower()
    out: list[dict[str, Any]] = []
    for proj_meta in storage.list_projects():
        pid = proj_meta["id"]
        try:
            sb_path = storage.project_dir(pid) / "soundbites.json"
            if not sb_path.exists():
                continue
            data = storage.read_json(pid, "soundbites.json")
        except Exception:
            continue
        for bite in data.get("soundbites", []):
            text = (bite.get("text") or "") + " " + (bite.get("summary") or "")
            if needle in text.lower():
                out.append({
                    "project_id": pid,
                    "project_name": proj_meta["name"],
                    "soundbite": bite,
                })
                if len(out) >= limit:
                    return out
    return out


@app.get("/api/stats")
async def stats() -> dict[str, Any]:
    """Server-wide stats: project count, total disk usage, presets count."""
    import shutil as _sh
    total_bytes = 0
    project_count = 0
    bites_total = 0
    renders_total = 0
    for child in storage.PROJECTS_DIR.iterdir():
        if not child.is_dir() or child.name.startswith("_") or child.name.startswith("."):
            continue
        project_count += 1
        for f in child.rglob("*"):
            if f.is_file():
                try:
                    total_bytes += f.stat().st_size
                except OSError:
                    pass
        try:
            sb = child / "soundbites.json"
            if sb.exists():
                data = _json.loads(sb.read_text())
                bites_total += len(data.get("soundbites") or [])
        except Exception:
            pass
        renders_total += len(list((child / "exports").glob("*.mp4")) if (child / "exports").exists() else [])

    presets = brand_presets.list_presets()
    disk = _sh.disk_usage(str(storage.PROJECTS_DIR))
    return {
        "projects": project_count,
        "soundbites": bites_total,
        "renders": renders_total,
        "presets": len(presets),
        "bytes_used": total_bytes,
        "disk_total": disk.total,
        "disk_free": disk.free,
        "version": app.version,
    }


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---- static SPA --------------------------------------------------------------

WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
