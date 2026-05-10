"""Subject-aware reframing for aspect-ratio changes.

Strategy (cheap and good-enough):
  1. Sample N frames from the source.
  2. Send them to GPT-4o vision and ask for a normalized horizontal anchor
     (0.0..1.0) for the speaker / main subject in each frame.
  3. Take the median anchor → crop centered on that x-position.

This avoids OpenCV / mediapipe dependencies and works for talking-head
shots, screen recordings, products, and most landscapes.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from statistics import median

from openai import AsyncOpenAI

from .ffmpeg import duration as ffduration, run


_SYSTEM = """For each frame, return a JSON object with the horizontal center
of the main subject as a normalized x in [0, 1]. 0 = far left, 1 = far right.

If a person is in the frame, anchor on their face. Otherwise, anchor on the
visual focal point (product, action, text).

Reply with ONLY a JSON object: {"frames": [{"x": 0.42}, {"x": 0.5}, ...]}
in the same order as the input images."""


async def _sample_frames(src: Path, n: int = 5) -> list[Path]:
    dur = await ffduration(src)
    n = max(1, min(n, 8))
    timestamps = [(dur * (i + 1)) / (n + 1) for i in range(n)]
    out_dir = Path(tempfile.mkdtemp(prefix="hf-smartcrop-"))
    paths: list[Path] = []
    for i, t in enumerate(timestamps):
        out = out_dir / f"frame_{i:02d}.jpg"
        await run([
            "ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(src),
            "-frames:v", "1", "-q:v", "3", "-vf", "scale=480:-2",
            str(out),
        ])
        if out.exists():
            paths.append(out)
    return paths


def _encode(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


async def detect_anchor_x(src: Path, *, frames: int = 5) -> float:
    """Returns the median normalized horizontal anchor in [0, 1]."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        # No key → fall back to centered crop
        return 0.5

    sampled = await _sample_frames(src, n=frames)
    if not sampled:
        return 0.5
    try:
        content = [{"type": "text", "text": "Frames in chronological order."}]
        for p in sampled:
            content.append({"type": "image_url", "image_url": {"url": _encode(p), "detail": "low"}})
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": content},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    finally:
        shutil.rmtree(sampled[0].parent, ignore_errors=True)

    xs: list[float] = []
    for f in data.get("frames") or []:
        try:
            v = float(f.get("x"))
            if 0.0 <= v <= 1.0:
                xs.append(v)
        except (TypeError, ValueError):
            continue
    if not xs:
        return 0.5
    return float(median(xs))
