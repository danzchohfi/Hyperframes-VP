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

    # Build the filter_complex: source becomes [v0]; each PNG is faded
    # in/out and overlaid on the running base.
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(source)]
    for png in png_paths:
        cmd += ["-i", str(png)]

    parts: list[str] = []
    parts.append(f"[0:v]scale={target_w}:{target_h},format=yuva420p[v0]")

    base_label = "v0"
    for i, raw in enumerate(animations):
        anim = ra.normalize_animation(raw, duration)
        idx = i + 1  # ffmpeg input index
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

    # Mark each PNG input as a looping single-frame source so overlay's
    # timeline runs for the duration of the video. Done via -loop 1 and
    # -t <duration> per input; the filter graph handles enable= cropping.
    # We add these flags by re-stitching the cmd: insert before each "-i".
    rebuilt: list[str] = []
    it = iter(cmd)
    rebuilt.append(next(it))  # ffmpeg
    rebuilt.append(next(it))  # -y
    rebuilt.append(next(it))  # -i
    rebuilt.append(next(it))  # source path
    while True:
        try:
            flag = next(it)
        except StopIteration:
            break
        # `flag` should be "-i"
        path = next(it)
        rebuilt += ["-loop", "1", "-t", f"{duration:.3f}", "-i", path]
    cmd = rebuilt

    filter_complex = ";".join(parts)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{base_label}]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
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
