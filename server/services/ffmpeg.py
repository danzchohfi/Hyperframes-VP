"""Thin async wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


async def run(cmd: list[str], *, timeout: float | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FFmpegError(f"timed out: {' '.join(shlex.quote(c) for c in cmd)}")
    if proc.returncode != 0:
        raise FFmpegError(
            f"{cmd[0]} exited {proc.returncode}\nCMD: {' '.join(shlex.quote(c) for c in cmd)}\nSTDERR:\n{stderr.decode(errors='ignore')}"
        )
    return stdout.decode(errors="ignore")


async def probe(path: Path) -> dict:
    out = await run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    return json.loads(out)


async def duration(path: Path) -> float:
    info = await probe(path)
    fmt = info.get("format", {})
    if "duration" in fmt:
        return float(fmt["duration"])
    for s in info.get("streams", []):
        if "duration" in s:
            return float(s["duration"])
    return 0.0


async def normalize(src: Path, dst: Path) -> None:
    """Re-encode upload to a deterministic mp4 (H.264 + AAC) for the pipeline."""
    await run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    )


async def detect_silences(src: Path, *, noise_db: float = -32.0, min_silence: float = 0.5) -> list[tuple[float, float]]:
    """Return list of (start, end) silence intervals in seconds. silencedetect writes to stderr."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(src),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    log = stderr.decode(errors="ignore")
    starts: list[float] = []
    ends: list[float] = []
    for line in log.splitlines():
        if "silence_start:" in line:
            try:
                starts.append(float(line.split("silence_start:")[1].strip().split()[0]))
            except (ValueError, IndexError):
                pass
        elif "silence_end:" in line:
            try:
                ends.append(float(line.split("silence_end:")[1].strip().split()[0]))
            except (ValueError, IndexError):
                pass
    return list(zip(starts, ends))


async def cut_segments(src: Path, dst: Path, keep: list[tuple[float, float]], *, lut: Path | None = None) -> None:
    """Concatenate the kept segments into dst, optionally applying a 3D LUT."""
    if not keep:
        raise FFmpegError("no segments to keep")

    inputs: list[str] = []
    for start, end in keep:
        inputs += ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src)]

    parts: list[str] = []
    n = len(keep)
    for i in range(n):
        v = f"[{i}:v]"
        if lut is not None:
            v = f"[{i}:v]lut3d={shlex.quote(str(lut))}[v{i}];[v{i}]"
        parts.append(f"{v}[{i}:a:0]")
    concat = "".join(parts) + f"concat=n={n}:v=1:a=1[v][a]"

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", concat,
           "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart",
           str(dst)]
    await run(cmd)


async def apply_lut(src: Path, dst: Path, lut: Path) -> None:
    await run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"lut3d={lut}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    )


async def to_aspect(src: Path, dst: Path, target: str) -> None:
    """Reframe to '9:16' or '16:9' or '1:1' with letterbox/crop fallback."""
    presets = {
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "1:1": (1080, 1080),
    }
    if target not in presets:
        raise FFmpegError(f"unsupported aspect: {target}")
    w, h = presets[target]
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )
    await run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    )
