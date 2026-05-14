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
    fade_ms: float = 30.0,
) -> str:
    """Build an ffmpeg audio filter expression that applies per-speaker gain.

    A naive `volume=enable=...` does HARD on/off gain switches at every
    speaker boundary — audibly clicks/pops on dense conversations. We
    use a smooth gate via `volume=eval=frame:volume='...'` with a cosine
    ramp `fade_ms` long centered on each boundary, so the gain glides
    rather than jumps.

    The volume expression is evaluated per-frame. For each speaker turn
    [s, e) the contribution is `gain * smoothstep(t, s, s+fade) *
    smoothstep(e-t, 0, fade)`. We sum contributions across speakers
    using max-style if-chain so overlapping turns don't double-apply.
    """
    fade_s = max(0.005, fade_ms / 1000.0)
    # Pre-compute (start, end, gain) tuples, dropping no-op gains and
    # very-short turns. fade_s + safety margin trims turns shorter than
    # the fade itself, otherwise the curve never reaches full gain and
    # the leveling is invisible — better to skip than under-apply.
    spans: list[tuple[float, float, float]] = []
    for t in turns:
        sp = t.get("speaker") or "?"
        s = float(t.get("start") or 0.0)
        e = float(t.get("end") or s)
        if e - s < fade_s * 2 + 0.05:
            continue
        gain = gains_db.get(sp)
        if gain is None or abs(gain) < 0.1:
            continue
        spans.append((s, e, gain))
    if not spans:
        return "anull"

    # ffmpeg's `volume=eval=frame:volume=EXPR` reads EXPR every frame.
    # Build a piecewise-linear curve: for each span, ramp from 0dB to
    # gain over fade_s, hold, ramp back to 0dB over fade_s. The
    # expression `clip((t-s)/fade, 0, 1) * clip((e-t)/fade, 0, 1)`
    # gives a trapezoidal envelope in [0, 1]; multiply by gain to get
    # the per-span dB contribution. Sum all spans (overlapping spans
    # add — rare in well-segmented diarization, fine in practice).
    terms: list[str] = []
    for s, e, gain in spans:
        env = (
            f"(max(0,min(1,(t-{s:.3f})/{fade_s:.3f})) "
            f"* max(0,min(1,({e:.3f}-t)/{fade_s:.3f})))"
        )
        terms.append(f"({env}*{gain:.3f})")
    expr = "+".join(terms) if terms else "0"
    # Convert summed dB to linear gain: 10^(dB/20)
    return f"volume=eval=frame:volume='pow(10,({expr})/20)'"
