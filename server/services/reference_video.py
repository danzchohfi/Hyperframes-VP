"""Analyze a user-uploaded reference video to extract editing style cues.

The user uploads a clip whose look they want the pipeline to emulate
(an Apple keynote, a MotionVFX-style trailer, a podcast they like…).
This module:

  1. Truncates the source to a configurable cap (default 60s).
  2. Extracts keyframes via ffmpeg scene-detection, with a uniform
     sampling fallback when scene cuts are too sparse.
  3. Pulls a compact audio track for Whisper to transcribe (delegated
     to the existing whisper module so we get its disk cache for free).
  4. Computes shot-pacing stats from the scene-cut timestamps.
  5. Calls Claude Sonnet 4.6 with vision (image_block per keyframe) +
     transcript + metadata, forcing a tool_use whose schema maps 1:1
     onto fields the rest of the pipeline already understands:
     style preset suggestion, easing default, cinematic toggles,
     prompt_hint for anim_planner.

A "deep" mode (opt-in via deep=True) additionally uploads the source
video itself through the Anthropic Files API so the model can see
motion between keyframes, not just the keyframes. Falls back to
"more keyframes" when Files API isn't available on the installed SDK.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

import anthropic

from . import ffmpeg as ff

log = logging.getLogger(__name__)


MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 2048

# Defaults — overridable per-call.
DEFAULT_MAX_DURATION = 60.0
DEFAULT_MAX_FRAMES = 16
DEEP_MAX_FRAMES = 32

# Scene-detect threshold — 0.30 is the sweet spot for podcast / vlog
# content (catches real cuts, ignores compression-noise jitter).
SCENE_THRESHOLD = 0.30

# Vision keyframes — downscale before encoding so a 60s reference at
# 1080p doesn't blow up the request payload. 720px wide preserves
# enough detail for style judgement (palette, layout, type weight).
KEYFRAME_WIDTH = 720

# Files API video cap (Anthropic limit at time of writing). When the
# deep upload would exceed this, we skip the upload and just extract
# more keyframes instead.
DEEP_VIDEO_MAX_BYTES = 30 * 1024 * 1024


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


async def truncate(src: Path, dst: Path, max_duration: float) -> None:
    """Copy the first `max_duration` seconds of src to dst, re-encoding
    so the cut frame is a keyframe and downstream seeks are accurate."""
    await ff.run([
        "ffmpeg", "-y",
        "-i", str(src),
        "-t", f"{max_duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ])


async def detect_scene_cuts(src: Path) -> list[float]:
    """Return [t_seconds, ...] timestamps of detected scene cuts."""
    # Runs ffmpeg with a scene-detect filter and parses showinfo lines.
    out = await ff.run([
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(src),
        "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
        "-an", "-f", "null", "-",
    ])
    cuts: list[float] = []
    for m in re.finditer(r"pts_time:([0-9.]+)", out):
        try:
            cuts.append(round(float(m.group(1)), 3))
        except ValueError:
            continue
    # showinfo also emits the very first frame; treat anything before
    # 0.2s as "the opening shot" rather than a real cut.
    cuts = [t for t in cuts if t > 0.2]
    cuts.sort()
    return cuts


async def extract_keyframes(
    src: Path,
    dst_dir: Path,
    *,
    max_frames: int,
    duration: float,
    scene_cuts: list[float] | None = None,
) -> list[Path]:
    """Write up to max_frames JPEGs of representative shots from src
    into dst_dir. Strategy:
      - If we have enough scene cuts, place one frame just after each
        cut (so we see the new shot rather than the wipe).
      - Top up with uniformly-spaced frames so we always end with
        max_frames thumbnails (so vision payload size is predictable).
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    times: list[float] = []
    if scene_cuts:
        for c in scene_cuts:
            times.append(min(duration - 0.05, c + 0.1))
    # uniformly fill the rest of the slots
    if len(times) < max_frames:
        gaps_needed = max_frames - len(times)
        # space remaining slots evenly across the duration, avoiding
        # the cuts we already picked
        used = set(int(t * 4) for t in times)
        cand: list[float] = []
        step = duration / (max_frames + 1)
        for i in range(1, max_frames * 2):
            t = round(i * step, 3)
            if t >= duration:
                break
            key = int(t * 4)
            if key in used:
                continue
            cand.append(t)
            used.add(key)
        times.extend(cand[:gaps_needed])
    times = sorted(set(round(t, 3) for t in times if 0.05 < t < duration))[:max_frames]
    if not times:
        # Pathological — single-shot 0.1s clip. Grab one frame at the middle.
        times = [duration / 2.0]

    paths: list[Path] = []
    for i, t in enumerate(times):
        dst = dst_dir / f"f{i:02d}.jpg"
        await ff.run([
            "ffmpeg", "-y",
            "-ss", f"{t:.3f}",
            "-i", str(src),
            "-frames:v", "1",
            "-vf", f"scale={KEYFRAME_WIDTH}:-2",
            "-q:v", "4",
            str(dst),
        ])
        if dst.exists():
            paths.append(dst)
    return paths


def _pacing_stats(scene_cuts: list[float], duration: float) -> dict[str, Any]:
    """Derive pacing summary the LLM can reason about."""
    if not scene_cuts or duration <= 0:
        return {"cuts": 0, "cuts_per_minute": 0.0, "avg_shot_seconds": duration}
    cpm = round(len(scene_cuts) / (duration / 60.0), 2)
    # avg shot length = duration / (cuts+1)  (cuts split into cuts+1 shots)
    avg_shot = round(duration / (len(scene_cuts) + 1), 2)
    # tempo bucket — used by the LLM as a quick cue
    if cpm >= 30:
        tempo = "very-fast"
    elif cpm >= 15:
        tempo = "fast"
    elif cpm >= 6:
        tempo = "medium"
    elif cpm >= 2:
        tempo = "slow"
    else:
        tempo = "very-slow"
    return {
        "cuts": len(scene_cuts),
        "cuts_per_minute": cpm,
        "avg_shot_seconds": avg_shot,
        "tempo": tempo,
    }


# ---------------------------------------------------------------------------
# Claude tool schema — mirrors fields that downstream code already
# consumes (style_presets ids, cinematic toggles, named easings).
# ---------------------------------------------------------------------------

def _analysis_tool() -> dict[str, Any]:
    from . import reels_animations as ra
    from . import style_presets as sp
    easing_names = sorted(ra.EASINGS.keys())
    preset_ids = list(sp.PRESETS.keys())
    return {
        "name": "emit_reference_analysis",
        "description": (
            "Given the supplied keyframes (and optional video) of a reference "
            "edit, emit a structured style readout that downstream pipeline "
            "stages can consume directly. Field semantics map 1:1 onto the "
            "existing animation planner and composer arguments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "2-3 sentence prose summary of the editing style.",
                },
                "edit_style": {
                    "type": "string",
                    "description": "Short label: 'apple-keynote' / 'motionvfx-cinema' / 'documentary' / 'youtube-vlog' / 'tiktok-fast-cut' / etc.",
                },
                "dominant_palette": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 4 hex colors that dominate the reference.",
                    "maxItems": 4,
                },
                "typography_notes": {
                    "type": "string",
                    "description": "What the type design looks like (weight, tracking, serif/sans, kinetic vs static).",
                },
                "pacing": {
                    "type": "string",
                    "enum": ["very-slow", "slow", "medium", "fast", "very-fast"],
                },
                "easing_suggestion": {
                    "type": "string",
                    "enum": easing_names,
                    "description": "Which named easing best matches what you see (or hear) in the reference.",
                },
                "cinematic": {
                    "type": "object",
                    "description": "Which cinematic overlay layers the reference uses.",
                    "properties": {
                        "grain": {"type": "boolean"},
                        "vignette": {"type": "boolean"},
                        "light_leaks": {"type": "boolean"},
                        "chromatic_aberration": {"type": "boolean"},
                        "bloom_emphasis": {"type": "boolean"},
                        "chapter_transition": {
                            "type": "string",
                            "enum": ["whip", "flash", "glitch", "dissolve", "none"],
                        },
                    },
                },
                "preset_suggestion": {
                    "type": "string",
                    "enum": preset_ids,
                    "description": "Which built-in style preset best matches the reference.",
                },
                "prompt_hint": {
                    "type": "string",
                    "description": (
                        "Free-form sentence the animation planner should treat "
                        "as a style directive. 1-3 sentences, imperative voice. "
                        "Reference specific design choices you observed."
                    ),
                },
            },
            "required": [
                "summary", "edit_style", "pacing",
                "easing_suggestion", "cinematic",
                "preset_suggestion", "prompt_hint",
            ],
        },
    }


SYSTEM_PROMPT = """You are a senior motion-design director reviewing a
reference video that the user wants to emulate.

You receive:
- A grid of keyframes pulled from the reference (downscaled to 720px).
- Optionally the source video itself (when deep mode is on).
- The reference's audio transcript.
- Computed metadata: duration, scene-cut count, cuts-per-minute, tempo.

Your job is to fill out the emit_reference_analysis tool with concrete
field values the rest of our pipeline consumes:

- `easing_suggestion`: pick the named easing that best matches the
  reference's motion language. apple-emphasis / apple-decel for soft
  premium feel, cinema-punch for overshoot impact, swift-release for
  fast-in slow-settle, glide-in for documentary-style hero reveals.
- `cinematic`: set each boolean truthfully. Don't claim grain is on
  unless you can SEE film grain in the keyframes. Same for vignette
  (vignetted edges), chromatic aberration (rgb-split text),
  bloom_emphasis (glowing highlights), light_leaks (gradient sweeps
  between shots).
- `chapter_transition`: which transition kind (whip/flash/glitch/
  dissolve/none) matches the cuts you see. If shots cut hard with no
  effect, pick "none". If there's a quick white frame between shots,
  "flash". Visible rgb-split + jitter → "glitch". Motion-blurred
  sweeping cut → "whip". Slow opacity blend → "dissolve".
- `preset_suggestion`: pick whichever built-in preset is closest.
  apple-keynote = minimal premium. motionvfx-cinema = punchy
  cinematic. documentary = NYT/Netflix doc. indie-tech = bold
  gradients. When in doubt, the preset's name describes its identity.
- `prompt_hint`: write this as a directive an animation planner can
  use directly. Be concrete: "Hook with keynote variant + apple-decel,
  no emoji bursts, sparse callouts". NOT abstract ("make it feel
  cinematic").

Output exclusively through the tool — no prose."""


def _file_to_image_block(p: Path) -> dict[str, Any]:
    """Read a JPEG and wrap it in a content block for the API."""
    with p.open("rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
    }


def _upload_deep_video(client: anthropic.Anthropic, video: Path) -> str | None:
    """Try uploading the truncated source via the Files API for deep
    analysis. Returns the file_id, or None when the SDK build doesn't
    expose the upload (in which case the caller falls back to more
    keyframes). Best-effort: any error here is logged and swallowed."""
    if video.stat().st_size > DEEP_VIDEO_MAX_BYTES:
        log.info("reference_video: deep upload skipped (%.1f MB > cap)",
                 video.stat().st_size / 1e6)
        return None
    try:
        # The Files API lives under .beta on older builds and at the
        # top level on newer ones. Try both.
        upload_fn = None
        if hasattr(client, "beta") and hasattr(client.beta, "files"):
            upload_fn = client.beta.files.upload
        elif hasattr(client, "files"):
            upload_fn = client.files.upload
        if not upload_fn:
            return None
        with video.open("rb") as f:
            uploaded = upload_fn(file=(video.name, f, "video/mp4"))
        file_id = getattr(uploaded, "id", None) or getattr(uploaded, "file_id", None)
        return file_id
    except Exception as e:
        log.warning("reference_video: deep upload failed: %s", e)
        return None


def analyze(
    *,
    frames: list[Path],
    transcript: dict[str, Any] | None,
    metadata: dict[str, Any],
    deep_video: Path | None = None,
) -> dict[str, Any]:
    """Run the analysis call. Pure: takes paths + dicts, returns a dict."""
    client = _client()
    tool = _analysis_tool()

    # User-content block: [image, image, ..., text_summary]
    content: list[dict[str, Any]] = [_file_to_image_block(p) for p in frames]

    deep_block_added = False
    if deep_video and deep_video.exists():
        file_id = _upload_deep_video(client, deep_video)
        if file_id:
            content.append({
                "type": "document",
                "source": {"type": "file", "file_id": file_id},
            })
            deep_block_added = True

    transcript_text = ""
    if transcript:
        t = (transcript.get("text") or "").strip()
        if len(t) > 4000:
            t = t[:4000] + " […truncated]"
        transcript_text = t

    meta_text = (
        f"<reference_metadata>\n"
        f"duration: {metadata.get('duration', 0):.1f}s\n"
        f"keyframes: {len(frames)}\n"
        f"scene_cuts: {metadata.get('cuts', 0)}\n"
        f"cuts_per_minute: {metadata.get('cuts_per_minute', 0)}\n"
        f"avg_shot_seconds: {metadata.get('avg_shot_seconds', 0)}\n"
        f"tempo: {metadata.get('tempo', 'medium')}\n"
        f"deep_video_attached: {deep_block_added}\n"
        f"</reference_metadata>"
    )
    transcript_block = (
        f"\n\n<reference_transcript>\n{transcript_text}\n</reference_transcript>"
        if transcript_text else ""
    )
    content.append({
        "type": "text",
        "text": f"{meta_text}{transcript_block}",
    })

    system_blocks = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_blocks,
            tools=[tool],
            tool_choice={
                "type": "tool",
                "name": tool["name"],
                "disable_parallel_tool_use": True,
            },
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise RuntimeError("ANTHROPIC_API_KEY inválida ou ausente")
    except anthropic.BadRequestError as e:
        raise RuntimeError(f"Análise rejeitada pelo Claude: {e}")
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude API erro {getattr(e, 'status_code', '?')}: {e}")

    tool_block = next(
        (b for b in resp.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_block is None:
        raise RuntimeError("Claude não emitiu a análise (sem tool_use no retorno)")

    raw = dict(tool_block.input or {})
    # Stamp the metadata back into the saved analysis so the UI / API
    # consumers don't need to load three separate files.
    raw["_metadata"] = metadata
    raw["_frames"] = len(frames)
    raw["_deep_video"] = deep_block_added
    raw["_model"] = MODEL
    return raw


async def build_analysis(
    *,
    src: Path,
    project_dir: Path,
    max_duration: float = DEFAULT_MAX_DURATION,
    deep: bool = False,
) -> dict[str, Any]:
    """End-to-end orchestration: truncate + scene-detect + keyframes +
    transcript + Claude call. Persists `reference_analysis.json` and
    returns the parsed analysis dict.

    `src` should already be the uploaded reference path (project_dir /
    'reference' / 'video.mp4'); this function writes alongside it.
    """
    ref_dir = project_dir / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    work_video = ref_dir / "truncated.mp4"
    await truncate(src, work_video, max_duration)
    actual_duration = await ff.duration(work_video)

    cuts = await detect_scene_cuts(work_video)
    pacing = _pacing_stats(cuts, actual_duration)
    pacing["duration"] = round(actual_duration, 2)

    frames_dir = ref_dir / "frames"
    if frames_dir.exists():
        for old in frames_dir.glob("*.jpg"):
            old.unlink()
    max_frames = DEEP_MAX_FRAMES if deep else DEFAULT_MAX_FRAMES
    frames = await extract_keyframes(
        work_video, frames_dir,
        max_frames=max_frames, duration=actual_duration, scene_cuts=cuts,
    )
    if not frames:
        raise RuntimeError("Nenhum keyframe extraído — vídeo de referência corrompido?")

    # Transcript — reuse the project-wide cache by routing through
    # whisper.transcribe(). On short clips (≤60s) this is one cheap
    # call. We swallow Whisper errors so a broken/missing audio track
    # still lets the vision side of the analysis run.
    transcript: dict[str, Any] | None = None
    try:
        from . import whisper
        transcript = await whisper.transcribe(work_video)
    except Exception as e:
        log.warning("reference_video: transcript skipped: %s", e)

    import asyncio
    deep_path = work_video if deep else None
    analysis = await asyncio.to_thread(
        analyze,
        frames=frames, transcript=transcript,
        metadata=pacing, deep_video=deep_path,
    )

    (project_dir / "reference_analysis.json").write_text(json.dumps(analysis, indent=2))
    return analysis
