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


async def cut_segments(
    src: Path,
    dst: Path,
    keep: list[tuple[float, float]],
    *,
    lut: Path | None = None,
    loudnorm: bool = False,
    denoise: bool = False,
) -> None:
    """Concatenate the kept segments into dst, optionally applying a 3D LUT
    and/or audio polish (loudnorm broadcast target + light denoise)."""
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

    audio_chain = ["[a]"]
    if denoise:
        audio_chain.append("afftdn=nf=-25[a1]")
    if loudnorm:
        # Single-pass online loudnorm targeting -14 LUFS (social-media spec).
        audio_chain.append(("loudnorm=I=-14:LRA=11:TP=-1.5") + "[a2]")
    if len(audio_chain) > 1:
        # rebuild as filter chain after concat
        chain_filters: list[str] = []
        cur_in = "[a]"
        steps = audio_chain[1:]
        for i, step in enumerate(steps):
            out_label = step.split("[")[-1].split("]")[0]
            f = step.split("[")[0]
            chain_filters.append(f"{cur_in}{f}[{out_label}]")
            cur_in = f"[{out_label}]"
        full = concat + ";" + ";".join(chain_filters)
        audio_map = cur_in
    else:
        full = concat
        audio_map = "[a]"

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", full,
           "-map", "[v]", "-map", audio_map,
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


async def concat_segments_from_multiple(
    items: list[tuple[Path, float, float]],
    dst: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    loudnorm: bool = False,
    crossfade: float = 0.0,
) -> None:
    """Concatenate (path, start, end) tuples from multiple files into one MP4.

    Each segment is scaled to the target frame size with padding so different
    source aspect ratios don't break the concat.
    """
    if not items:
        raise FFmpegError("no items")

    inputs: list[str] = []
    for path, s, e in items:
        inputs += ["-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", str(path)]

    # Per-segment normalize chain (scale + pad + audio resample)
    parts: list[str] = []
    for i, _ in enumerate(items):
        parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}];"
            f"[{i}:a:0]aresample=async=1[a{i}]"
        )
    chain = ";".join(parts)

    cf = float(crossfade or 0.0)
    if cf > 0.01 and len(items) >= 2:
        # Chain xfade between consecutive segments.
        # Each segment's effective duration after trim is (end - start).
        durs = [max(0.04, e - s) for (_, s, e) in items]
        # cumulative offset for xfade: sum(prev_durs) - cf * (idx - 0)
        # but xfade uses absolute offset from start of the previous (chained) stream.
        # Simpler: incrementally build, tracking the chain's current total length.
        xchain_parts: list[str] = []
        cur_v = "v0"
        cur_a = "a0"
        cur_len = durs[0]
        for i in range(1, len(items)):
            next_v = f"v{i}"
            next_a = f"a{i}"
            out_v = f"vx{i}"
            out_a = f"ax{i}"
            offset = max(0.0, cur_len - cf)
            xchain_parts.append(
                f"[{cur_v}][{next_v}]xfade=transition=fade:duration={cf:.3f}:offset={offset:.3f}[{out_v}];"
                f"[{cur_a}][{next_a}]acrossfade=d={cf:.3f}[{out_a}]"
            )
            cur_v = out_v
            cur_a = out_a
            cur_len = cur_len + durs[i] - cf
        chain += ";" + ";".join(xchain_parts)
        v_label = f"[{cur_v}]"
        a_label = f"[{cur_a}]"
        full = chain + f";{v_label}null[v];{a_label}anull[a]"
    else:
        concat = "".join(f"[v{i}][a{i}]" for i in range(len(items))) + f"concat=n={len(items)}:v=1:a=1[v][a]"
        full = chain + ";" + concat

    audio_map = "[a]"
    if loudnorm:
        full += ";[a]loudnorm=I=-14:LRA=11:TP=-1.5[al]"
        audio_map = "[al]"

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", full,
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ]
    await run(cmd)


async def apply_audio_filter(src: Path, dst: Path, audio_filter: str) -> None:
    """Re-encode `src` to `dst` applying `audio_filter` to its audio. Video
    is stream-copied."""
    await run([
        "ffmpeg", "-y", "-i", str(src),
        "-af", audio_filter,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ])


async def mix_music(
    voice_src: Path,
    music_src: Path,
    dst: Path,
    *,
    music_db: float = -8.0,
    duck_threshold: float = 0.05,
    duck_ratio: float = 8.0,
) -> None:
    """Mix a music track under a voice track with sidechain ducking.

    The music is attenuated whenever the voice is loud, so dialogue stays
    clear. Voice goes through unchanged.
    """
    voice_dur = await duration(voice_src)
    music_dur = await duration(music_src)

    # Loop music if shorter than voice; trim if longer
    music_input = ["-stream_loop", "-1", "-t", f"{voice_dur:.3f}", "-i", str(music_src)] \
        if music_dur < voice_dur else ["-i", str(music_src)]

    fc = (
        f"[1:a]volume={music_db}dB[m];"
        f"[m][0:a]sidechaincompress=threshold={duck_threshold}:ratio={duck_ratio}:attack=20:release=300[ducked];"
        f"[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=0[a]"
    )
    cmd = ["ffmpeg", "-y",
           "-i", str(voice_src),
           *music_input,
           "-filter_complex", fc,
           "-map", "0:v?", "-map", "[a]",
           "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k",
           "-shortest",
           "-movflags", "+faststart",
           str(dst)]
    await run(cmd)


async def burn_subtitles(src: Path, dst: Path, ass_path: Path) -> None:
    """Burn an ASS subtitle file into the video via ffmpeg's subtitles filter."""
    # ffmpeg's subtitles filter requires : and ' escaped in the path
    ass_arg = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    await run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", f"ass='{ass_arg}'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst),
    ])


async def extract_audio_with_chapters(
    src: Path,
    dst: Path,
    *,
    chapters: list[dict],
    title: str,
    artist: str,
    album: str | None = None,
    bitrate: str = "192k",
) -> None:
    """Extract MP3 with ID3 tags + chapter markers.

    `chapters` is [{name, start, end}, ...] in seconds. ffmpeg accepts an
    -f ffmetadata file describing them.
    """
    import tempfile
    meta_lines = [";FFMETADATA1"]
    meta_lines.append(f"title={_escape_meta(title)}")
    meta_lines.append(f"artist={_escape_meta(artist)}")
    if album:
        meta_lines.append(f"album={_escape_meta(album)}")
    for ch in chapters:
        s_ms = int(float(ch.get("start") or 0.0) * 1000)
        e_ms = int(float(ch.get("end") or 0.0) * 1000)
        if e_ms <= s_ms:
            continue
        meta_lines.append("[CHAPTER]")
        meta_lines.append("TIMEBASE=1/1000")
        meta_lines.append(f"START={s_ms}")
        meta_lines.append(f"END={e_ms}")
        meta_lines.append(f"title={_escape_meta(ch.get('name', 'Chapter'))}")

    with tempfile.NamedTemporaryFile("w", suffix=".meta", delete=False) as f:
        f.write("\n".join(meta_lines) + "\n")
        meta_path = f.name

    try:
        await run([
            "ffmpeg", "-y", "-i", str(src),
            "-i", meta_path,
            "-map_metadata", "1",
            "-vn",
            "-c:a", "libmp3lame", "-b:a", bitrate,
            str(dst),
        ])
    finally:
        Path(meta_path).unlink(missing_ok=True)


def _escape_meta(text: str) -> str:
    # ffmetadata escapes: =, ;, #, \, newline
    out = text.replace("\\", "\\\\").replace("=", r"\=").replace(";", r"\;").replace("#", r"\#")
    return out.replace("\n", " ")


async def extract_audio(src: Path, dst: Path, *, format: str = "mp3", bitrate: str = "192k") -> None:
    """Extract the audio stream as MP3 (default) or WAV."""
    if format == "mp3":
        codec = ["-c:a", "libmp3lame", "-b:a", bitrate]
    elif format == "wav":
        codec = ["-c:a", "pcm_s16le"]
    elif format == "m4a":
        codec = ["-c:a", "aac", "-b:a", bitrate]
    else:
        raise FFmpegError(f"unsupported audio format: {format}")
    await run([
        "ffmpeg", "-y", "-i", str(src),
        "-vn", *codec,
        str(dst),
    ])


async def grab_thumbnail(src: Path, dst: Path, *, at: float, width: int = 480) -> None:
    """Save a single still frame at `at` seconds as JPEG."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    await run([
        "ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(src),
        "-frames:v", "1", "-q:v", "3",
        "-vf", f"scale={width}:-2",
        str(dst),
    ])


async def to_aspect(src: Path, dst: Path, target: str) -> None:
    """Reframe to '9:16' or '16:9' or '1:1' with letterbox/pad fallback."""
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
    await run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst),
    ])


async def to_aspect_smart(src: Path, dst: Path, target: str, *, anchor_x: float = 0.5) -> None:
    """Subject-aware reframe: scales to fill (no letterbox), then crops centered
    on `anchor_x` (0..1, normalized horizontal position from smartcrop).
    """
    presets = {
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "1:1": (1080, 1080),
    }
    if target not in presets:
        raise FFmpegError(f"unsupported aspect: {target}")
    w, h = presets[target]

    # Scale to fill (cover) → crop centered on anchor_x.
    # We scale so that whichever dimension fits exactly, the other is larger,
    # then crop the excess.
    vf = (
        f"scale='if(gt(a,{w}/{h}),-2,{w})':'if(gt(a,{w}/{h}),{h},-2)':flags=lanczos,"
        f"crop={w}:{h}:max(0\\,min(iw-{w}\\,iw*{anchor_x:.4f}-{w}/2)):"
        f"max(0\\,min(ih-{h}\\,ih/2-{h}/2)),setsar=1"
    )
    await run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst),
    ])
