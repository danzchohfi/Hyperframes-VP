"""Procedurally-generated SFX library for reel animations.

We use ffmpeg's `aevalsrc` filter to synthesize short UI sounds (whoosh,
ding, pop…) from sine + noise + envelopes. No external assets, no
licensing concerns — the recipes here are the only "library". Each SFX
is rendered once on demand and cached at
`server/assets/sfx/{name}.mp3`; subsequent calls just return the path.

To extend: add a new entry to SFX_RECIPES and the UI dropdown will pick
it up automatically (it queries `list_presets()`).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .ffmpeg import run

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "sfx"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


# (duration_seconds, label, aevalsrc expression)
# The expression evaluates `t` from 0 to duration and outputs a sample
# in roughly [-1, 1]. We keep amplitudes < 0.7 so amix doesn't clip.
SFX_RECIPES: dict[str, tuple[float, str, str]] = {
    "ding": (
        0.55, "Sino — agudo curto",
        "0.5*sin(2*PI*1200*t)*exp(-t*4)+0.25*sin(2*PI*2400*t)*exp(-t*6)",
    ),
    "pop": (
        0.15, "Pop — burst rápido",
        "0.7*sin(2*PI*(140+t*40)*t)*exp(-t*22)",
    ),
    "drop": (
        0.55, "Drop — bass descendo",
        "0.65*sin(2*PI*(300-t*420)*t)*exp(-t*3)",
    ),
    "whoosh-up": (
        0.45, "Whoosh subindo",
        "0.55*(random(0)-0.5)*if(lt(t,0.05),t*20,1)*exp(-t*2.4)",
    ),
    "whoosh-down": (
        0.45, "Whoosh descendo",
        "0.55*(random(0)-0.5)*if(gt(t,0.4),(0.45-t)*20,1)*exp(-t*2.4)",
    ),
    "swipe": (
        0.35, "Swipe — passada filtrada",
        "0.55*(random(0)-0.5)*sin(PI*t/0.35)",
    ),
    "riser": (
        1.0, "Riser — tensão crescente",
        "0.4*sin(2*PI*(120+t*t*900)*t)*if(lt(t,0.95),t,1)",
    ),
    "chime": (
        0.95, "Chime — três sinos",
        "0.32*sin(2*PI*1568*t)*exp(-t*3)+0.22*sin(2*PI*1976*t)*exp(-t*4)+0.14*sin(2*PI*2349*t)*exp(-t*5)",
    ),
}


# A sensible default SFX per animation type. Overridable per animation.
DEFAULT_SFX_FOR_TYPE: dict[str, str] = {
    "hook_card":      "whoosh-up",
    "cta_end":        "chime",
    "text_callout":   "pop",
    "word_zoom":      "pop",
    "number_pop":     "ding",
    "lower_third":    "swipe",
    "emoji_burst":    "ding",
    "arrow_highlight":"swipe",
}


def list_presets() -> list[dict[str, str | float]]:
    """For the UI dropdown."""
    return [
        {"name": name, "label": label, "duration": dur}
        for name, (dur, label, _expr) in SFX_RECIPES.items()
    ]


async def get_or_create_sfx(name: str) -> Path:
    """Return the MP3 path for `name`, synthesizing it on first call."""
    if name not in SFX_RECIPES:
        raise KeyError(f"unknown SFX preset: {name}")
    out = ASSETS_DIR / f"{name}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out
    dur, _label, expr = SFX_RECIPES[name]
    # Render via lavfi → mono 44.1kHz MP3. ffmpeg's filter syntax uses
    # both ':' (option separator) and ',' (filter / multi-channel
    # separator), and aevalsrc's `exprs` parameter expects expressions
    # joined by '|' if multiple. We escape both so the entire recipe
    # stays one expression for the single (mono) channel.
    safe = expr.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")
    await run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"aevalsrc=exprs={safe}:d={dur}:s=44100",
        "-ac", "1", "-ar", "44100",
        "-c:a", "libmp3lame", "-q:a", "5",
        str(out),
    ])
    log.info("synthesized SFX preset %s → %s", name, out)
    return out


async def prebuild_all() -> dict[str, Path]:
    """Eagerly create every preset (used on first startup or after a wipe)."""
    out: dict[str, Path] = {}
    for name in SFX_RECIPES:
        try:
            out[name] = await get_or_create_sfx(name)
        except Exception:
            log.exception("failed to synthesize SFX %s", name)
    return out


def get_path_if_built(name: str) -> Path | None:
    """Synchronous lookup — None if not built yet."""
    p = ASSETS_DIR / f"{name}.mp3"
    return p if (p.exists() and p.stat().st_size > 0) else None


async def get_paths_for_animations(animations: list[dict]) -> dict[str, Path]:
    """For each animation that has `sfx`, ensure the file exists; return a
    map of preset_name → Path so the renderer can reuse the same file
    across multiple animations."""
    needed: set[str] = set()
    for a in animations or []:
        sfx = a.get("sfx")
        if sfx and sfx in SFX_RECIPES:
            needed.add(sfx)
    out: dict[str, Path] = {}
    for name in needed:
        try:
            out[name] = await get_or_create_sfx(name)
        except Exception:
            log.exception("SFX %s unavailable, skipping", name)
    return out
