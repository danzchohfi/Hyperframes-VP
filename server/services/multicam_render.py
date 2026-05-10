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

Output goes to <project>/exports/multicam-roughcut.mp4.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from . import ffmpeg as ff


async def render(
    *,
    project_dir: Path,
    source: Path,
    angle_paths: list[Path],         # angle_paths[0] = source mirror, [1..] = uploads
    angle_offsets: list[float],       # in seconds; positive means clip starts later than source
    plan: list[dict[str, Any]],       # [{start, end, angle_index}, ...]  (source timecode)
    out: Path,
) -> Path:
    if not plan:
        raise RuntimeError("empty plan")

    # Build the ffmpeg command:
    #   -i source.mp4         (audio reference)
    #   -i angle1.mp4
    #   ...
    # filter_complex:
    #   For each segment k: take [angle_input:v]trim=start=Sk-off:end=Ek-off,setpts=PTS-STARTPTS[vk];
    #   Concat all vk's.
    # Map: [vout] + [0:a] trimmed/filtered to the union [Sk:Ek] (same as source span).

    inputs: list[str] = []
    # angle[0] is source itself
    for path in angle_paths:
        inputs += ["-i", str(path)]

    filter_parts: list[str] = []
    # Video chains per segment
    video_labels: list[str] = []
    for k, c in enumerate(plan):
        ai = int(c.get("angle_index", 0))
        s = float(c["start"])
        e = float(c["end"])
        off = angle_offsets[ai] if 0 <= ai < len(angle_offsets) else 0.0
        # angle clock = source clock - offset  (an angle that lags by off shows source-time T at angle-time T-off)
        a_in = max(0.0, s - off)
        a_out = max(a_in + 0.04, e - off)
        v_label = f"v{k}"
        filter_parts.append(
            f"[{ai}:v]trim=start={a_in:.3f}:end={a_out:.3f},"
            f"setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[{v_label}]"
        )
        video_labels.append(f"[{v_label}]")

    # Concatenate video
    filter_parts.append(
        "".join(video_labels) + f"concat=n={len(plan)}:v=1:a=0[vout]"
    )
    # For audio, build a parallel concat from source [0:a] for the same windows
    audio_labels: list[str] = []
    for k, c in enumerate(plan):
        s = float(c["start"])
        e = float(c["end"])
        a_label = f"a{k}"
        filter_parts.append(
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[{a_label}]"
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
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    await ff.run(cmd)
    return out
