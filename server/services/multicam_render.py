"""Render a multicam edit by cutting each interval from the chosen angle.

Given a camera plan (list of {start, end, angle_index}) and the angle pool
(source + uploaded angles, each with audio_offset for sync), build an ffmpeg
filter that:
  - For each cut, takes [angle_in : angle_out] from the chosen angle's file.
    angle_in/out are remapped through audio_offset: when an angle starts
    `offset` seconds later than source, we trim `offset` from the angle's
    beginning to align with the source's timeline.
  - Always uses the SOURCE's audio (single reference track), so the cut feels
    locked even if other angles have different audio.
  - Concatenates the kept video segments.

Output goes to <project>/exports/<name>-multicam.mp4.

A few non-obvious correctness bits the concat path depends on:
- Every video chain forces fps=30 + scale 1920x1080 + sar=1, otherwise
  concat refuses dissimilar streams or accumulates drift across segments
  when the angles have different native frame rates.
- The audio is fully re-sampled to 48 kHz stereo before concat for the
  same reason.
- We clamp each angle trim to its actual duration so a long source
  segment that overruns a shorter angle doesn't hang ffmpeg.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ffmpeg as ff


TARGET_FPS = 30
TARGET_W, TARGET_H = 1920, 1080
TARGET_SR = 48000


async def _probe_duration(path: Path) -> float:
    try:
        info = await ff.probe(path)
    except Exception:
        return 0.0
    fmt = info.get("format") or {}
    try:
        return float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ff_path_escape(p: Path) -> str:
    """Escape a path so it can sit inside an ffmpeg filter_complex value.
    Inside a filter chain ffmpeg parses ':' as an option separator and '\'
    as the escape char, so a LUT path with colons (Windows drive letters,
    URLs) or spaces would otherwise break parsing.
    """
    s = str(p)
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    return s


async def render(
    *,
    project_dir: Path,
    source: Path,
    angle_paths: list[Path],         # angle_paths[0] = source mirror, [1..] = uploads
    angle_offsets: list[float],       # in seconds; positive means clip starts later than source
    plan: list[dict[str, Any]],       # [{start, end, angle_index}, ...]  (source timecode)
    out: Path,
    lut: Path | None = None,         # optional .cube — applied to every video chain
) -> Path:
    if not plan:
        raise RuntimeError("empty plan")

    # Probe every angle once so we can clamp trim ranges to actual durations
    # and avoid ffmpeg hangs when a segment overruns a shorter angle.
    durations: list[float] = []
    for p in angle_paths:
        durations.append(await _probe_duration(p))

    inputs: list[str] = []
    for path in angle_paths:
        inputs += ["-i", str(path)]

    filter_parts: list[str] = []

    # Per-segment video chains. Each is trimmed from its chosen angle and
    # then normalized to TARGET_FPS / TARGET_W x TARGET_H / SAR 1 so concat
    # can splice them without drift.
    video_labels: list[str] = []
    for k, c in enumerate(plan):
        ai = int(c.get("angle_index", 0))
        if ai < 0 or ai >= len(angle_paths):
            ai = 0
        s = float(c["start"])
        e = float(c["end"])
        off = angle_offsets[ai] if 0 <= ai < len(angle_offsets) else 0.0
        a_in = max(0.0, s - off)
        a_out = max(a_in + 0.04, e - off)
        # Clamp to angle's actual duration so trim doesn't run past EOF.
        ang_dur = durations[ai] if ai < len(durations) else 0.0
        if ang_dur > 0:
            a_in = min(a_in, max(0.0, ang_dur - 0.04))
            a_out = min(a_out, ang_dur)
            if a_out <= a_in + 0.04:
                # Segment falls entirely outside angle's available footage.
                # Fall back to source for this cut so the timeline doesn't
                # have a gap (and audio stays in sync).
                ai = 0
                off = angle_offsets[0]
                a_in = max(0.0, s - off)
                a_out = max(a_in + 0.04, e - off)
        v_label = f"v{k}"
        lut_clause = f",lut3d=file='{_ff_path_escape(lut)}'" if lut else ""
        filter_parts.append(
            f"[{ai}:v]trim=start={a_in:.3f}:end={a_out:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={TARGET_FPS}{lut_clause}[{v_label}]"
        )
        video_labels.append(f"[{v_label}]")

    filter_parts.append(
        "".join(video_labels) + f"concat=n={len(plan)}:v=1:a=0[vout]"
    )

    # Per-segment audio chains. Always taken from SOURCE (input 0), which
    # is the canonical timeline. Source segments share the same input so
    # they already have identical sample-rate/channel-layout; we just
    # atrim + asetpts (PTS-STARTPTS) so concat can splice them. The
    # aresample/aformat dance is left to the AAC encoder at the output
    # stage — adding it inside the filter graph caused certain ffmpeg
    # builds to emit MP4s that QuickTime refused to open.
    audio_labels: list[str] = []
    for k, c in enumerate(plan):
        s = float(c["start"])
        e = float(c["end"])
        a_label = f"a{k}"
        filter_parts.append(
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},"
            f"asetpts=PTS-STARTPTS[{a_label}]"
        )
        audio_labels.append(f"[{a_label}]")
    filter_parts.append(
        "".join(audio_labels) + f"concat=n={len(plan)}:v=0:a=1[aout]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        *ff._x264_args(),
        "-pix_fmt", "yuv420p",
        "-r", str(TARGET_FPS),
        "-c:a", "aac", "-b:a", "192k",
        "-ar", str(TARGET_SR), "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]
    await ff.run(cmd)

    # Sanity-check the output: ffmpeg occasionally exits 0 on a
    # filter-graph hiccup with a moov-less / truncated MP4 that won't
    # open in QuickTime. If ffprobe can't read it back, raise so the
    # user sees a clear error instead of getting a broken download.
    if not out.exists() or out.stat().st_size < 1024:
        raise RuntimeError("multicam render produced an empty file")
    try:
        info = await ff.probe(out)
    except Exception as e:
        raise RuntimeError(f"output mp4 unreadable ({e}); ffmpeg likely failed mid-write")
    streams = info.get("streams") or []
    has_v = any(s.get("codec_type") == "video" for s in streams)
    has_a = any(s.get("codec_type") == "audio" for s in streams)
    if not (has_v and has_a):
        raise RuntimeError(
            f"output mp4 missing streams (video={has_v}, audio={has_a}); "
            f"check the filter_complex"
        )
    return out
