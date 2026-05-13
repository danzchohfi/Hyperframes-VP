"""Global reel-animation templates.

A template is a list of animations with *relative* timing so it can be
applied to any source video regardless of length.

Each animation carries:
  - `start_mode`: "from_start" | "from_end" | "percent" | "absolute"
  - `start_offset`: seconds (from_*) or 0..1 (percent) — the meaning
    depends on start_mode.
  - Plus the regular fields (type, duration, text, sub, emoji, variant,
    show_logo, anchor).

When applying:
  - The destination video's `duration` is used to resolve start_mode →
    a concrete `start` in seconds.
  - Text fields support `{brand.name}` and `{brand.tagline}`
    substitution so the same template feels native to any project.

Built-in templates are merged with user-saved ones in `list_templates()`,
flagged with `builtin=True`. Saved templates live in
`server/projects/_reel_templates/`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "projects" / "_reel_templates"
ROOT.mkdir(parents=True, exist_ok=True)


# --- built-in starter templates --------------------------------------------

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "_id": "_builtin_intro_cta",
        "name": "Intro + CTA",
        "description": "Hook no início + CTA no fim com logo da marca. O básico de todo reel.",
        "builtin": True,
        "animations": [
            {
                "type": "hook_card", "variant": "bold",
                "duration": 1.4, "anchor": "center",
                "start_mode": "from_start", "start_offset": 0.0,
                "text": "{brand.intro_title}", "sub": "{brand.tagline}",
                "show_logo": True,
            },
            {
                "type": "cta_end", "variant": "gradient",
                "duration": 2.0, "anchor": "center",
                "start_mode": "from_end", "start_offset": 0.0,
                "text": "{brand.outro_text}", "sub": "{brand.tagline}",
                "show_logo": True,
            },
        ],
    },
    {
        "_id": "_builtin_vlog_playful",
        "name": "Vlog playful",
        "description": "Hook gradient + emoji-burst no meio + callout perto do fim + CTA minimal.",
        "builtin": True,
        "animations": [
            {
                "type": "hook_card", "variant": "gradient",
                "duration": 1.6, "anchor": "center",
                "start_mode": "from_start", "start_offset": 0.0,
                "text": "{brand.intro_title}", "show_logo": True,
            },
            {
                "type": "emoji_burst",
                "duration": 1.0, "anchor": "center-right",
                "start_mode": "percent", "start_offset": 0.2,
                "emoji": "🔥",
            },
            {
                "type": "text_callout", "variant": "pill",
                "duration": 1.6, "anchor": "bottom-left",
                "start_mode": "percent", "start_offset": 0.55,
                "text": "Segue {brand.name}", "emoji": "👉",
            },
            {
                "type": "cta_end", "variant": "minimal",
                "duration": 2.0, "anchor": "center",
                "start_mode": "from_end", "start_offset": 0.0,
                "text": "{brand.outro_text}", "show_logo": False,
            },
        ],
    },
    {
        "_id": "_builtin_podcast_cinematic",
        "name": "Podcast cinematic",
        "description": "Hook discreto + lower-third 4s no começo da conversa + CTA bold no final.",
        "builtin": True,
        "animations": [
            {
                "type": "hook_card", "variant": "minimal",
                "duration": 1.5, "anchor": "center",
                "start_mode": "from_start", "start_offset": 0.0,
                "text": "{brand.intro_title}", "sub": "{brand.tagline}",
                "show_logo": False,
            },
            {
                "type": "lower_third",
                "duration": 4.0, "anchor": "bottom-left",
                "start_mode": "percent", "start_offset": 0.05,
                "text": "{brand.name}", "sub": "{brand.tagline}",
            },
            {
                "type": "cta_end", "variant": "bold",
                "duration": 2.2, "anchor": "center",
                "start_mode": "from_end", "start_offset": 0.0,
                "text": "{brand.outro_text}",
                "show_logo": True,
            },
        ],
    },
]


# --- helpers ----------------------------------------------------------------

def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", (name or "template").lower()).strip("-")
    return s or "template"


def _substitute(text: str | None, brand: dict[str, Any] | None) -> str | None:
    if not text or not brand:
        return text
    name = (brand.get("name") or "voce").strip()
    out = (
        text.replace("{brand.name}", name)
            .replace("{brand.tagline}", (brand.get("tagline") or "").strip() or name)
            .replace("{brand.intro_title}", (brand.get("intro_title") or name or "Watch this").strip())
            .replace("{brand.outro_text}", (brand.get("outro_text") or f"Siga @{name.lower()}").strip())
    )
    return out


def to_template_animation(anim: dict, duration: float) -> dict:
    """Convert a project's animation (absolute timing) into a relative-timing
    template entry. The heuristic picks the most natural start_mode:

      - Near the start (<2s)          → from_start
      - Ending near the end (within 1s) → from_end
      - Otherwise                     → percent
    """
    a = dict(anim or {})
    start = float(a.get("start") or 0.0)
    dur = float(a.get("duration") or 1.5)
    end = start + dur
    a.pop("start", None)
    a.pop("source", None)
    a.pop("reason", None)
    if duration and end >= duration - 1.0:
        a["start_mode"] = "from_end"
        a["start_offset"] = max(0.0, round(duration - end, 3))
    elif start < 2.0:
        a["start_mode"] = "from_start"
        a["start_offset"] = round(start, 3)
    elif duration and duration > 0:
        a["start_mode"] = "percent"
        a["start_offset"] = round(start / duration, 4)
    else:
        a["start_mode"] = "absolute"
        a["start_offset"] = round(start, 3)
    return a


def from_template_animation(
    anim: dict, target_duration: float, brand: dict[str, Any] | None = None,
) -> dict:
    """Resolve a relative-timing template entry against a target duration."""
    a = dict(anim or {})
    mode = a.pop("start_mode", "absolute")
    offset = float(a.pop("start_offset", a.get("start") or 0.0))
    dur = max(0.3, float(a.get("duration") or 1.5))

    if mode == "from_start":
        start = max(0.0, offset)
    elif mode == "from_end":
        start = max(0.0, target_duration - offset - dur)
    elif mode == "percent":
        start = max(0.0, target_duration * max(0.0, min(1.0, offset)))
    else:  # absolute
        start = max(0.0, offset)

    # Clamp inside the video.
    start = min(start, max(0.0, target_duration - 0.3))
    dur = min(dur, max(0.3, target_duration - start))

    a["start"] = round(start, 3)
    a["duration"] = round(dur, 3)
    a["text"] = _substitute(a.get("text"), brand)
    a["sub"] = _substitute(a.get("sub"), brand)
    a["source"] = "template"
    return a


# --- store ------------------------------------------------------------------

def list_templates() -> list[dict[str, Any]]:
    """Built-ins first, then user-saved (most recent at the bottom)."""
    out: list[dict[str, Any]] = [dict(t) for t in BUILTIN_TEMPLATES]
    for child in sorted(ROOT.glob("*.json")):
        try:
            data = json.loads(child.read_text())
            data["_id"] = child.stem
            data["builtin"] = False
            out.append(data)
        except Exception:
            continue
    return out


def get_template(tid: str) -> dict[str, Any]:
    for t in BUILTIN_TEMPLATES:
        if t.get("_id") == tid:
            return dict(t)
    path = ROOT / f"{tid}.json"
    if not path.exists():
        raise FileNotFoundError(tid)
    data = json.loads(path.read_text())
    data["_id"] = tid
    data["builtin"] = False
    return data


def save_template(
    name: str,
    description: str,
    animations: list[dict],
    duration: float,
) -> dict[str, Any]:
    tid = _slug(name)
    # Avoid clobbering a built-in by accident.
    if tid.startswith("_builtin_"):
        tid = "user_" + tid
    rel = [to_template_animation(a, duration) for a in animations]
    payload = {
        "name": name,
        "description": description or "",
        "animations": rel,
    }
    (ROOT / f"{tid}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False)
    )
    payload["_id"] = tid
    payload["builtin"] = False
    return payload


def delete_template(tid: str) -> bool:
    if tid.startswith("_builtin_"):
        return False
    path = ROOT / f"{tid}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def apply_template(
    tid: str, target_duration: float, brand: dict[str, Any] | None = None,
) -> list[dict]:
    """Return the animations resolved for a project of the given duration."""
    tpl = get_template(tid)
    return [
        from_template_animation(a, target_duration, brand)
        for a in (tpl.get("animations") or [])
    ]
