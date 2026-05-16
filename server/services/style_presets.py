"""Curated style presets for end-to-end "look" choices.

Each preset bundles together choices that go across multiple layers:
- which easings the animation planner should prefer,
- which hook/CTA variant to default to,
- which cinematic overlays (grain / vignette / light leaks / chromatic /
  bloom) to enable on the composer,
- which chapter transition kind to fire,
- a free-form `prompt_hint` the LLM sees as part of the planner's
  user prompt so it can match the look in places code can't pre-set
  (text wording, density, palette suggestions, etc).

The presets are pure data — they neither read project files nor touch
disk. Endpoints / pipelines consume them by name; the UI lists them
via GET /api/animation-presets.
"""

from __future__ import annotations

from typing import Any


PRESETS: dict[str, dict[str, Any]] = {
    "apple-keynote": {
        "label": "Apple Keynote",
        "summary": "Premium minimal — keynote typography, soft easings, no grain.",
        "easing_default": "apple-decel",
        "hook_variant": "keynote",
        "cta_variant": "keynote",
        "cinematic": {
            "grain": False,
            "vignette": True,
            "light_leaks": False,
            "chromatic_aberration": False,
            "bloom_emphasis": True,
            "chapter_transition": "dissolve",
        },
        "prompt_hint": (
            "Match an Apple Keynote: minimal, premium, lots of negative space. "
            "Hook and CTA use the 'keynote' variant with 'apple-decel' or "
            "'apple-emphasis' easing. Few callouts; let titles breathe. No "
            "emoji bursts. Soft palette, no neon. Captions are conservative."
        ),
    },
    "motionvfx-cinema": {
        "label": "MotionVFX Cinema",
        "summary": "Bold cinematic — punchy easings, glitch cuts, grain, chromatic hits.",
        "easing_default": "cinema-punch",
        "hook_variant": "bold",
        "cta_variant": "bold",
        "cinematic": {
            "grain": True,
            "vignette": True,
            "light_leaks": True,
            "chromatic_aberration": True,
            "bloom_emphasis": True,
            "chapter_transition": "glitch",
        },
        "prompt_hint": (
            "MotionVFX-style cinematic energy. Use 'cinema-punch' easing on "
            "hook and emphasis, 'swift-release' elsewhere. Lean into impact "
            "moments: word_zoom and number_pop on every big number/quote. "
            "1-2 emoji bursts max on the comedic peaks. Bold gradient or "
            "minimal hook (NOT keynote). Punchy 3-6 word callouts."
        ),
    },
    "documentary": {
        "label": "Documentary",
        "summary": "NYT / Netflix doc — slow dissolves, sepia vignette, plenty of lower-thirds.",
        "easing_default": "glide-in",
        "hook_variant": "minimal",
        "cta_variant": "minimal",
        "cinematic": {
            "grain": True,
            "vignette": True,
            "light_leaks": False,
            "chromatic_aberration": False,
            "bloom_emphasis": False,
            "chapter_transition": "dissolve",
        },
        "prompt_hint": (
            "Documentary / long-form interview style. Lots of lower-thirds "
            "for name+role identification. Slow 'glide-in' or 'apple-decel' "
            "easing. No emoji bursts, no chromatic aberration. Hook and CTA "
            "are minimal — typography over decoration. Callouts are sparse "
            "and substantive (quotes from the transcript, not editorial)."
        ),
    },
    "indie-tech": {
        "label": "Indie Tech",
        "summary": "Bold gradients, fast eases, neon glow — startup launch energy.",
        "easing_default": "swift-release",
        "hook_variant": "gradient",
        "cta_variant": "gradient",
        "cinematic": {
            "grain": False,
            "vignette": False,
            "light_leaks": True,
            "chromatic_aberration": False,
            "bloom_emphasis": True,
            "chapter_transition": "flash",
        },
        "prompt_hint": (
            "Indie tech launch energy — Vercel / Linear / Raycast vibe. Bold "
            "gradient hook and CTA. Fast 'swift-release' / 'power4.out' eases. "
            "Emphasize numbers and product names with word_zoom + bloom. "
            "Captions punchy, hook 4-6 words, CTA 2-4 words."
        ),
    },
}


def list_presets() -> list[dict[str, Any]]:
    """Return all presets as a list — convenient for the UI dropdown.
    Stripped of the prompt_hint (which the LLM consumes, not the user)."""
    out: list[dict[str, Any]] = []
    for key, p in PRESETS.items():
        out.append({
            "id": key,
            "label": p["label"],
            "summary": p["summary"],
            "easing_default": p["easing_default"],
            "hook_variant": p["hook_variant"],
            "cta_variant": p["cta_variant"],
            "cinematic": p["cinematic"],
        })
    return out


def get_preset(preset_id: str | None) -> dict[str, Any] | None:
    if not preset_id:
        return None
    return PRESETS.get(preset_id)


def apply_to_animations(
    animations: list[dict[str, Any]],
    preset: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Walk a list of normalized animations and rewrite easing /
    variant fields to match the preset, when those fields haven't
    been set explicitly. Returns a new list; mutates a copy.

    The rule: if the animation already declares a non-default easing
    or variant, leave it alone — the user / LLM made an intentional
    choice. We only fill in the gaps.
    """
    if not preset:
        return animations
    ease = preset.get("easing_default")
    hook_variant = preset.get("hook_variant")
    cta_variant = preset.get("cta_variant")

    out: list[dict[str, Any]] = []
    for a in animations:
        a2 = dict(a)
        t = a2.get("type")
        # Apply preset variant to hook / CTA when no variant was set
        # explicitly. normalize_animation defaults to "bold" for those
        # types, so we treat "bold" as "unset" — otherwise the preset
        # would never win.
        if t == "hook_card" and hook_variant and a2.get("variant", "bold") == "bold":
            a2["variant"] = hook_variant
        if t == "cta_end" and cta_variant and a2.get("variant", "bold") == "bold":
            a2["variant"] = cta_variant
        # Apply preset easing when the animation didn't carry one and
        # the type has no opinionated default of its own.
        if ease and not a2.get("easing"):
            a2["easing"] = ease
        out.append(a2)
    return out
