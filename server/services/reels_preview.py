"""Fast MP4 preview of reel animations via PIL + ffmpeg.

Two-step pipeline:
  1. PIL rasterizes each animation into a full-frame transparent PNG
     at the animation's peak state.
  2. ffmpeg overlays each PNG on top of the source video with a
     0.25s fade-in + 0.3s fade-out, scoped by `enable='between(t,s,e)'`.

The whole call should finish in ~10s for a 30s reel on a modern laptop.
This is intentionally lower fidelity than the full Hyperframes render —
no GSAP tweens, no captions, no intro/outro card. Iteration speed > polish.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from .brand import BrandBook
from .ffmpeg import probe, run
from . import reels_animations as ra
from . import reels_rasterizer as rast
from . import sfx_lib
from . import tts as tts_svc


log = logging.getLogger(__name__)


# --- small helpers ----------------------------------------------------------

def _scaled_dims(aspect: str, target_w: int) -> tuple[int, int]:
    """Map an aspect string to (width, height) preserving target_w."""
    if aspect == "9:16":
        return (target_w, int(target_w * 16 / 9))
    if aspect == "1:1":
        return (target_w, target_w)
    return (target_w, int(target_w * 9 / 16))  # 16:9 default


def _ff_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# --- main entry point -------------------------------------------------------

async def render_quick_preview(
    *,
    project_dir: Path,
    source: Path,
    animations: list[dict[str, Any]],
    brand: BrandBook,
    width: int = 540,
    aspect: str | None = None,
    fps: int = 30,
) -> dict[str, Any]:
    """Compose a low-res preview MP4 with the animations baked in.

    Returns: {"path": Path, "took_ms": int, "duration": float,
              "rasterized": int, "reused": int}
    """
    if not source.exists():
        raise FileNotFoundError(str(source))

    t0 = time.monotonic()

    # Probe the source to know its real aspect; we still output at `width`
    # but pick a height that matches whatever the source already is unless
    # the caller overrode with `aspect`.
    info = await probe(source)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    sw = int(v.get("width") or 1920)
    sh = int(v.get("height") or 1080)
    if aspect:
        target_w, target_h = _scaled_dims(aspect, width)
    else:
        # Honor the source's aspect: scale down to width px wide.
        target_w = width
        target_h = max(2, int(round(width * sh / max(sw, 1))))
        # Force even (yuv420p needs it)
        if target_h % 2 == 1:
            target_h += 1
    duration = float(info.get("format", {}).get("duration") or 0.0)

    # Pre-rasterize each animation. Reuse cached PNG when the content hash
    # matches what we already have on disk.
    cache_dir = project_dir / ".reels-preview"
    cache_dir.mkdir(exist_ok=True)
    palette_dump = brand.palette.model_dump() if brand and brand.palette else None
    png_paths: list[Path] = []
    rasterized = 0
    reused = 0
    for i, raw in enumerate(animations):
        anim = ra.normalize_animation(raw, duration)
        h = rast.hash_anim(anim, palette_dump)
        png = cache_dir / f"anim_{i:02d}_{h}_{target_w}x{target_h}.png"
        if not png.exists():
            img = rast.rasterize_animation(
                anim, brand, target_w, target_h, project_dir=project_dir,
            )
            img.save(png, format="PNG", optimize=True)
            rasterized += 1
        else:
            reused += 1
        png_paths.append(png)

    out = project_dir / "preview.mp4"

    # Trivial case: no animations — just rescale the source.
    if not animations:
        await run([
            "ffmpeg", "-y", "-i", str(source),
            "-vf", f"scale={target_w}:{target_h}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k",
            "-r", str(fps),
            "-movflags", "+faststart",
            str(out),
        ])
        took_ms = int((time.monotonic() - t0) * 1000)
        return {
            "path": out, "took_ms": took_ms, "duration": duration,
            "rasterized": 0, "reused": 0, "width": target_w, "height": target_h,
        }

    # Does the source have an audio stream? amix only works when at least
    # one input is real audio; we use this to decide the audio mix shape.
    has_source_audio = any(s.get("codec_type") == "audio" for s in streams)

    # Pre-build audio inputs for animations: SFX presets + TTS narration.
    # Both are optional. SFX files come from the global library (synthesized
    # once and reused); TTS files are per-text (per-project cache).
    sfx_cache: dict[str, Path] = await sfx_lib.get_paths_for_animations(
        [{**ra.normalize_animation(a, duration),
          "sfx": ra.resolve_default_sfx(ra.normalize_animation(a, duration))}
         for a in animations]
    )
    tts_cache: dict[str, Path] = {}
    try:
        normalized_for_tts = [ra.normalize_animation(a, duration) for a in animations]
        tts_cache = await tts_svc.synthesize_for_animations(normalized_for_tts, project_dir)
    except Exception:
        log.exception("tts batch synth failed — continuing without narration")

    # Per-animation audio descriptor: (input_path, start_seconds, volume).
    # We keep these in order so filter labels stay stable.
    audio_events: list[tuple[Path, float, float]] = []
    for i, raw in enumerate(animations):
        anim = ra.normalize_animation(raw, duration)
        # SFX
        sfx_name = ra.resolve_default_sfx(anim)
        if sfx_name and sfx_name in sfx_cache:
            audio_events.append((sfx_cache[sfx_name], float(anim["start"]),
                                 float(anim.get("sfx_volume") or 0.7)))
        # TTS narration
        tts_path = tts_cache.get(f"anim_{i}")
        if tts_path:
            audio_events.append((tts_path, float(anim["start"]),
                                 float(anim.get("tts_volume") or 1.0)))

    # Build the filter_complex: source becomes [v0]; each PNG is faded
    # in/out and overlaid on the running base.
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(source)]
    for png in png_paths:
        cmd += ["-i", str(png)]
    for audio_path, _start, _vol in audio_events:
        cmd += ["-i", str(audio_path)]

    parts: list[str] = []
    parts.append(f"[0:v]scale={target_w}:{target_h},format=yuva420p[v0]")

    base_label = "v0"
    for i, raw in enumerate(animations):
        anim = ra.normalize_animation(raw, duration)
        idx = i + 1  # ffmpeg input index for the PNG
        start = float(anim["start"])
        dur = float(anim["duration"])
        end = start + dur
        fade_in = min(0.25, dur * 0.3)
        fade_out = min(0.3, dur * 0.35)
        # PNG inputs are static images; loop them so they're valid for
        # the whole timeline. The enable= scopes the actual visibility.
        parts.append(
            f"[{idx}:v]format=yuva420p,"
            f"fade=t=in:alpha=1:st={start:.3f}:d={fade_in:.3f},"
            f"fade=t=out:alpha=1:st={max(0, end - fade_out):.3f}:d={fade_out:.3f}"
            f"[a{i}]"
        )
        next_label = f"o{i}"
        parts.append(
            f"[{base_label}][a{i}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})':eof_action=pass[{next_label}]"
        )
        base_label = next_label

    # Audio mix: delay each event to its start time, scale by volume,
    # then amix everything together with the source audio.
    audio_start_idx = 1 + len(png_paths)  # ffmpeg input index where audio inputs begin
    audio_labels: list[str] = []
    for i, (_p, start_s, vol) in enumerate(audio_events):
        idx = audio_start_idx + i
        delay_ms = int(round(start_s * 1000))
        parts.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={vol:.2f}[evt{i}]"
        )
        audio_labels.append(f"[evt{i}]")

    # Final audio: source audio (when present) + every event.
    if audio_labels and has_source_audio:
        parts.append(
            f"[0:a]{''.join(audio_labels)}amix=inputs={1 + len(audio_labels)}:normalize=0:dropout_transition=0[aout]"
        )
        audio_map = ["-map", "[aout]"]
    elif audio_labels:
        # Source has no audio — just mix the events together (or single).
        if len(audio_labels) == 1:
            parts.append(f"{audio_labels[0]}acopy[aout]")
        else:
            parts.append(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:normalize=0:dropout_transition=0[aout]"
            )
        audio_map = ["-map", "[aout]"]
    else:
        audio_map = ["-map", "0:a?"]

    # Mark each PNG input as a looping single-frame source so overlay's
    # timeline runs for the duration of the video.
    rebuilt: list[str] = ["ffmpeg", "-y", "-i", str(source)]
    for png in png_paths:
        rebuilt += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(png)]
    for audio_path, _start, _vol in audio_events:
        rebuilt += ["-i", str(audio_path)]
    cmd = rebuilt

    filter_complex = ";".join(parts)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{base_label}]",
        *audio_map,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-r", str(fps),
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]

    try:
        await run(cmd)
    except Exception:
        # If anything blows up in filter_complex (e.g. very many overlays
        # on an old ffmpeg), fall back to a passthrough so the user still
        # gets something playable rather than a hard error.
        log.exception("quick preview filter_complex failed, falling back to passthrough scale")
        await run([
            "ffmpeg", "-y", "-i", str(source),
            "-vf", f"scale={target_w}:{target_h}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-r", str(fps),
            "-movflags", "+faststart",
            str(out),
        ])

    took_ms = int((time.monotonic() - t0) * 1000)
    return {
        "path": out,
        "took_ms": took_ms,
        "duration": duration,
        "rasterized": rasterized,
        "reused": reused,
        "width": target_w,
        "height": target_h,
    }


def prune_cache(project_dir: Path) -> None:
    """Wipe the rasterizer cache for a project — used when the brand
    palette changes since all PNGs are now potentially stale."""
    d = project_dir / ".reels-preview"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
