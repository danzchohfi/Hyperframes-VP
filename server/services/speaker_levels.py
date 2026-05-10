"""Per-speaker volume normalization.

For each speaker turn, measure mean_volume with ffmpeg volumedetect, then
compute a per-speaker gain so all speakers hit a target level (default
-18 dBFS mean). Build an ffmpeg filter that applies the gain only inside
each speaker's intervals.

This is cheap and handles the most common podcast issue: the host louder
than the guest (or vice versa).
"""

from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path

_VOL_RE = re.compile(r"mean_volume:\s*([\-\d\.]+)\s*dB")


async def _mean_volume(src: Path, *, start: float, end: float) -> float | None:
    """Return mean dBFS of `src` between start..end. None if no audio."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(src),
        "-af", "volumedetect",
        "-vn", "-sn", "-dn", "-f", "null", "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    log = err.decode(errors="ignore")
    m = _VOL_RE.search(log)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


async def measure_per_speaker(src: Path, turns: list[dict]) -> dict[str, float]:
    """Return {speaker: average_mean_dBFS} aggregating each speaker's turns.
    Speakers with no measurable audio are omitted.
    """
    samples: dict[str, list[float]] = {}
    for t in turns:
        sp = t.get("speaker") or "?"
        start = float(t.get("start") or 0.0)
        end = float(t.get("end") or start)
        if end - start < 0.4:
            continue
        v = await _mean_volume(src, start=start, end=end)
        if v is None:
            continue
        samples.setdefault(sp, []).append(v)
    return {sp: sum(vals) / len(vals) for sp, vals in samples.items() if vals}


def gain_plan(levels: dict[str, float], target_dbfs: float = -18.0) -> dict[str, float]:
    """Compute the gain (in dB) to apply per speaker so each hits target_dbfs."""
    return {sp: round(target_dbfs - v, 2) for sp, v in levels.items()}


def build_filter(
    turns: list[dict],
    gains_db: dict[str, float],
    *,
    crossfade: float = 0.05,
) -> str:
    """Build an ffmpeg audio filter expression that applies per-speaker gain
    using `volume` with `enable` expressions.

    Example:
        volume=enable='between(t,2.5,7.1)':volume=2dB,
        volume=enable='between(t,7.1,12.0)':volume=-1dB
    """
    parts: list[str] = []
    for t in turns:
        sp = t.get("speaker") or "?"
        s = float(t.get("start") or 0.0)
        e = float(t.get("end") or s)
        if e - s < 0.2:
            continue
        gain = gains_db.get(sp)
        if gain is None or abs(gain) < 0.1:
            continue
        # Convert dB → linear and apply as enable'd volume filter
        parts.append(f"volume=enable='between(t,{s:.3f},{e:.3f})':volume={gain:.2f}dB")
    if not parts:
        return "anull"
    return ",".join(parts)
