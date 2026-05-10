"""Compose a YouTube-style 1280x720 thumbnail with text overlay.

Uses a base frame from the source (e.g., the peak frame) + an ffmpeg drawtext
overlay with a brand-coloured title. No PIL dependency — pure ffmpeg.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from . import ffmpeg as ff


async def compose(
    *,
    source: Path,
    at: float,
    out: Path,
    title: str,
    sub: str | None = None,
    primary_hex: str = "#facc15",  # text color (yellow)
    background_dim: float = 0.5,    # 0..1 darkening for the base frame
) -> None:
    """Build the thumbnail. Source is sampled at `at` seconds; title + optional
    sub are drawn left-aligned, big, with a dark gradient overlay on the
    bottom for legibility.
    """
    title_color = _hex_to_ffmpeg(primary_hex)
    sub_color = "white@0.9"
    box_color = f"black@{background_dim:.2f}"

    # Layered filter:
    # 1. scale + pad to 1280x720
    # 2. apply darkening box on bottom 60% via geq or a brightness curve
    # 3. drawtext for title (huge, bold) and sub (smaller, weight 400)
    title_safe = _escape_text(title)
    filters = [
        "scale=1280:720:force_original_aspect_ratio=increase",
        "crop=1280:720",
        # Darken bottom band for legibility
        "drawbox=x=0:y=420:w=1280:h=300:color=" + box_color + ":t=fill",
        # Title (big)
        f"drawtext=text='{title_safe}':fontcolor={title_color}:fontsize=72:"
        "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        "x=60:y=480:shadowcolor=black@0.85:shadowx=3:shadowy=3",
    ]
    if sub:
        sub_safe = _escape_text(sub)
        filters.append(
            f"drawtext=text='{sub_safe}':fontcolor={sub_color}:fontsize=36:"
            "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            "x=60:y=600:shadowcolor=black@0.85:shadowx=2:shadowy=2"
        )

    vf = ",".join(filters)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{at:.3f}", "-i", str(source),
        "-frames:v", "1", "-q:v", "2",
        "-vf", vf,
        str(out),
    ]
    await ff.run(cmd)


def _hex_to_ffmpeg(h: str) -> str:
    """Convert '#rrggbb' to ffmpeg's 0xRRGGBB format."""
    h = h.lstrip("#")
    if len(h) == 6:
        return f"0x{h.upper()}"
    return "white"


def _escape_text(text: str) -> str:
    """Escape special chars for ffmpeg drawtext."""
    # ffmpeg drawtext needs : , \ % escaped + single quotes wrap
    return (
        text.replace("\\", r"\\\\")
            .replace(":", r"\:")
            .replace("%", r"\%")
            .replace(",", r"\,")
            .replace("'", r"\'")
    )
