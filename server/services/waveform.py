"""Generate downsampled audio peaks (RMS approximation) for UI visualization.

Uses ffmpeg's `astats` filter via `ebur128`-style sampling. We render the audio
to a low-rate signed-16 wav, then bucket the absolute samples.
"""

from __future__ import annotations

import struct
from pathlib import Path

from .ffmpeg import run


async def peaks(src: Path, *, buckets: int = 800, sample_rate: int = 4000) -> list[float]:
    """Returns `buckets` floats in [0, 1] representing RMS-ish loudness."""
    buckets = max(50, min(buckets, 4000))
    sample_rate = max(1000, min(sample_rate, 16000))

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        await run([
            "ffmpeg", "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "s16le", "-acodec", "pcm_s16le",
            str(tmp_path),
        ])
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)

    if not data:
        return [0.0] * buckets

    sample_count = len(data) // 2
    fmt = f"<{sample_count}h"
    samples = struct.unpack(fmt, data[: sample_count * 2])
    if not samples:
        return [0.0] * buckets

    bucket_size = max(1, sample_count // buckets)
    out: list[float] = []
    for i in range(0, sample_count, bucket_size):
        chunk = samples[i:i + bucket_size]
        if not chunk:
            break
        # mean abs as proxy for loudness
        s = sum(abs(x) for x in chunk) / len(chunk)
        out.append(round(min(1.0, s / 32768.0), 4))
        if len(out) >= buckets:
            break
    # pad to exact buckets
    while len(out) < buckets:
        out.append(0.0)
    return out
