"""ffmpeg-only audio enhancement for podcast voice.

A single async function `enhance(src, dst)` that runs a 4-stage filter
chain on the audio of `src` and writes to `dst` (keeping the video as
a stream copy so we don't re-encode):

  highpass     — cuts rumble below 80 Hz (AC, desk vibration)
  afftdn       — spectral denoise, -25 dB noise floor (conservative)
  acompressor  — moderate compressor for voice (3:1, attack 20, rel 200)
  loudnorm     — target -16 LUFS (Spotify/Apple podcast spec)

This is intentionally *not* trying to compete with Auphonic or
Adobe Podcast on the truly noisy end of the spectrum — it's the
"sound levels guard rail" for well-recorded podcasts that just need
a bit of polish. When a paid backend is wanted, we can add it as a
parallel module without changing callers.
"""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg as ff


VOICE_FILTER_CHAIN = (
    "highpass=f=80,"
    "afftdn=nf=-25,"
    "acompressor=threshold=-18dB:ratio=3:attack=20:release=200,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


async def enhance(src: Path, dst: Path) -> Path:
    """Apply the voice cleanup filter chain. Video stream is copied
    verbatim, audio is re-encoded to AAC 192k. Caller is responsible
    for picking the right `src` (typically the last upstream artifact —
    levelled.mp4 if it exists, else graded.mp4, else source.mp4) and
    for setting `state.has_enhanced = True` after a successful run.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    await ff.run([
        "ffmpeg", "-y",
        "-i", str(src),
        "-c:v", "copy",
        "-af", VOICE_FILTER_CHAIN,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ])
    if not dst.exists() or dst.stat().st_size < 1024:
        raise RuntimeError("audio enhance produced an empty file")
    return dst
