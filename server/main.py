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

from . import storage
from .services import ffmpeg as ff
from .services import whisper, silence, composer, render, fcpxml, music
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


class RenderIn(BaseModel):
    aspect: str = "9:16"  # 9:16 | 16:9 | 1:1
    include_captions: bool = True


class ExportIn(BaseModel):
    aspect: str = "9:16"


class FcpxmlIn(BaseModel):
    multicam: bool = False
    primary_angle: int = 0
    include_word_markers: bool = True
    use_cuts: bool = True


class AngleIn(BaseModel):
    name: str = "Angle"


class MusicSearchIn(BaseModel):
    suggestion: dict[str, Any] | None = None
    limit: int = 10


class MusicSelectIn(BaseModel):
    track: dict[str, Any]


# ---- helpers -----------------------------------------------------------------

def _stage(state: storage.ProjectState, name: str, status: str, msg: str | None = None) -> None:
    s = state.stage(name)
    s.status = status
    if msg is not None:
        s.message = msg
    if status == "running":
        s.started_at = storage._now()
    elif status in ("done", "error"):
        s.finished_at = storage._now()
    storage.save(state)


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
    return _load(pid).model_dump()


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

@app.post("/api/projects/{pid}/transcribe")
async def transcribe(pid: str) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise HTTPException(400, "no source uploaded")

    _stage(state, "transcribe", "running", "calling Whisper API")
    try:
        result = await whisper.transcribe(src)
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
async def apply_edits(pid: str) -> dict[str, Any]:
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

        lut_path = pdir / "lut.cube" if state.has_lut else None
        out_path = pdir / "graded.mp4"
        await ff.cut_segments(src, out_path, keep, lut=lut_path)
        out_dur = await ff.duration(out_path)
    except Exception as e:
        _stage(state, "apply", "error", str(e))
        raise HTTPException(500, str(e))

    _stage(state, "apply", "done", f"{out_dur:.2f}s edited")
    return {"duration": out_dur, "lut_applied": state.has_lut}


# ---- render via Hyperframes --------------------------------------------------

@app.post("/api/projects/{pid}/render")
async def do_render(pid: str, body: RenderIn) -> dict[str, Any]:
    state = _load(pid)
    pdir = storage.project_dir(pid)

    edited = pdir / "graded.mp4"
    if not edited.exists():
        edited = pdir / "source.mp4"
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

    _stage(state, "render", "running", "building composition")
    try:
        dur = await ff.duration(edited)
        comp_dir = composer.build_composition(
            project_dir=pdir,
            video_path=edited,
            video_duration=dur,
            transcript=transcript,
            brand=brand,
            aspect=body.aspect,
        )
    except Exception as e:
        _stage(state, "render", "error", f"compose: {e}")
        raise HTTPException(500, str(e))

    try:
        _stage(state, "render", "running", "running hyperframes render")
        out = await render.render(comp_dir, output_dir=pdir / "exports", name="render")
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
    if body.use_cuts and state.has_cuts:
        plan = storage.read_json(pid, "cuts.json")
        keep = [(seg["start"], seg["end"]) for seg in plan["keep"]]

    transcript = (
        storage.read_json(pid, "transcript.json")
        if (state.has_transcript and body.include_word_markers)
        else None
    )

    try:
        if body.multicam and state.angles:
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


# ---- file serving ------------------------------------------------------------

@app.get("/api/projects/{pid}/files/{name}")
async def serve_file(pid: str, name: str):
    pdir = storage.project_dir(pid)
    fp = (pdir / name).resolve()
    if not fp.exists() or pdir.resolve() not in fp.parents:
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


@app.get("/api/projects/{pid}/cuts")
async def get_cuts(pid: str):
    state = _load(pid)
    if not state.has_cuts:
        raise HTTPException(404, "no cuts plan")
    return JSONResponse(storage.read_json(pid, "cuts.json"))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---- static SPA --------------------------------------------------------------

WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
