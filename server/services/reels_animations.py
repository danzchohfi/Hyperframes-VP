"""Reusable animated overlays for reels.

Each builder returns a self-contained snippet of HTML + a tiny piece of
JS that registers a GSAP tween on the composition's main timeline. They
are written as Hyperframes clips (data-start / data-duration /
data-track-index / class="clip") and respect the project's brand palette.

The composer calls `build_animation_html(brand, anim, index, width)` to
turn a `RellAnimation` dict into HTML; the JS for each animation type
lives in `JS_BY_TYPE` and gets concatenated into the composer's
`<script>` block.
"""

from __future__ import annotations

import html
from typing import Any

from .brand import BrandBook


# --- supported animation types ---------------------------------------------

# Each entry: {label, default_duration, default_anchor, ui_fields}
ANIMATION_TYPES: dict[str, dict[str, Any]] = {
    "hook_card": {
        "label": "Hook intro (full-screen)",
        "default_duration": 1.4,
        "default_anchor": "center",
        "fields": ["text", "sub"],
    },
    "cta_end": {
        "label": "CTA final",
        "default_duration": 2.0,
        "default_anchor": "center",
        "fields": ["text", "sub"],
    },
    "text_callout": {
        "label": "Callout em sticker",
        "default_duration": 1.6,
        "default_anchor": "bottom-left",
        "fields": ["text", "emoji"],
    },
    "word_zoom": {
        "label": "Palavra em destaque",
        "default_duration": 0.9,
        "default_anchor": "center",
        "fields": ["text"],
    },
    "lower_third": {
        "label": "Lower-third (nome + cargo)",
        "default_duration": 3.0,
        "default_anchor": "bottom-left",
        "fields": ["text", "sub"],
    },
    "emoji_burst": {
        "label": "Emoji burst",
        "default_duration": 1.0,
        "default_anchor": "center-right",
        "fields": ["emoji"],
    },
    "number_pop": {
        "label": "Número grande",
        "default_duration": 1.2,
        "default_anchor": "center",
        "fields": ["text", "sub"],
    },
    "arrow_highlight": {
        "label": "Seta + destaque",
        "default_duration": 1.4,
        "default_anchor": "bottom",
        "fields": ["text", "anchor"],
    },
}


def normalize_animation(anim: dict, source_duration: float | None = None) -> dict:
    """Apply defaults + clamp values. Returns a new dict."""
    a = dict(anim or {})
    t = a.get("type") or "text_callout"
    if t not in ANIMATION_TYPES:
        t = "text_callout"
    spec = ANIMATION_TYPES[t]
    a["type"] = t
    a["start"] = max(0.0, float(a.get("start") or 0.0))
    a["duration"] = max(0.3, float(a.get("duration") or spec["default_duration"]))
    if source_duration:
        a["start"] = min(a["start"], max(0.0, source_duration - 0.3))
        a["duration"] = min(a["duration"], max(0.3, source_duration - a["start"]))
    a["anchor"] = a.get("anchor") or spec["default_anchor"]
    a["text"] = (a.get("text") or "").strip()
    a["sub"] = (a.get("sub") or "").strip() or None
    a["emoji"] = (a.get("emoji") or "").strip() or None
    return a


# --- shared CSS (anchors / palette tokens) ---------------------------------

def shared_css(brand: BrandBook, width: int) -> str:
    p = brand.palette
    big = int(width * 0.085)
    med = int(width * 0.05)
    sml = int(width * 0.024)
    return f"""
      .reels-anim {{ position: absolute; z-index: 80; pointer-events: none; }}
      .reels-anim.anchor-center      {{ inset: 0; display:flex; align-items:center; justify-content:center; }}
      .reels-anim.anchor-top         {{ top: 8%; left:0; right:0; display:flex; justify-content:center; }}
      .reels-anim.anchor-bottom      {{ bottom: 14%; left:0; right:0; display:flex; justify-content:center; }}
      .reels-anim.anchor-bottom-left {{ bottom: 14%; left: 6%; }}
      .reels-anim.anchor-center-right{{ top: 36%; right: 8%; }}
      .reels-anim .ra-pill {{
        padding: 10px 18px;
        background: linear-gradient(135deg, {p.primary}, {p.accent});
        color: {p.foreground};
        font-weight: 700;
        font-size: {sml}px;
        letter-spacing: -0.01em;
        border-radius: 999px;
        box-shadow: 0 8px 24px {p.primary}55;
      }}
      .reels-anim .ra-card {{
        padding: 28px 36px;
        background: linear-gradient(160deg, {p.background}dd, {p.background}99);
        border: 1px solid {p.primary}55;
        border-radius: 18px;
        backdrop-filter: blur(14px);
        box-shadow: 0 24px 60px rgba(0,0,0,0.55);
        text-align: center;
      }}
      .reels-anim .ra-card .ra-title {{
        font-size: {med}px;
        font-weight: 800;
        color: {p.foreground};
        letter-spacing: -0.025em;
        line-height: 1.05;
      }}
      .reels-anim .ra-card .ra-sub {{
        font-size: {sml}px;
        font-weight: 500;
        color: {p.foreground}b0;
        margin-top: 8px;
      }}
      .reels-anim .ra-bigword {{
        font-size: {big}px;
        font-weight: 900;
        color: {p.accent};
        text-shadow: 0 6px 24px {p.accent}55, 0 2px 0 {p.background};
        letter-spacing: -0.03em;
        text-transform: uppercase;
      }}
      .reels-anim .ra-emoji {{ font-size: {big}px; line-height: 1; }}
      .reels-anim .ra-arrow {{
        width: 0; height: 0;
        border-left: 18px solid transparent;
        border-right: 18px solid transparent;
        border-bottom: 28px solid {p.accent};
        margin-right: 10px;
      }}
      .reels-anim .ra-arrow-row {{
        display: flex; align-items: center; gap: 8px;
        background: {p.background}cc; padding: 10px 16px;
        border-radius: 12px; border: 1px solid {p.accent}66;
      }}
      .reels-anim .ra-lt {{
        display: flex; flex-direction: column; gap: 2px;
        background: linear-gradient(90deg, {p.primary}cc, {p.secondary}55);
        padding: 10px 18px;
        border-radius: 12px;
        border-left: 4px solid {p.accent};
        box-shadow: 0 8px 24px rgba(0,0,0,0.4);
      }}
      .reels-anim .ra-lt .ra-lt-name {{
        color: {p.foreground}; font-weight: 700; font-size: {sml}px;
      }}
      .reels-anim .ra-lt .ra-lt-role {{
        color: {p.foreground}b0; font-weight: 500; font-size: {int(sml*0.85)}px;
      }}
    """


# --- per-type HTML builders ------------------------------------------------

def _safe(text: str) -> str:
    return html.escape(text or "")


def build_animation_html(anim: dict, index: int) -> str:
    """Return a single <div class='reels-anim clip ...'> for the animation."""
    t = anim["type"]
    aid = f"ra-{index}"
    cls = f"reels-anim clip anchor-{anim.get('anchor', 'center')}"
    common = (
        f'id="{aid}" class="{cls}" '
        f'data-start="{anim["start"]:.3f}" '
        f'data-duration="{anim["duration"]:.3f}" '
        f'data-track-index="{20 + index}" '
        f'data-anim-type="{t}"'
    )
    text = _safe(anim.get("text") or "")
    sub = _safe(anim.get("sub") or "")
    emoji = _safe(anim.get("emoji") or "")

    if t == "hook_card" or t == "cta_end":
        body = f'<div class="ra-card"><div class="ra-title">{text}</div>'
        if sub:
            body += f'<div class="ra-sub">{sub}</div>'
        body += "</div>"
        return f'<div {common}>{body}</div>'

    if t == "text_callout":
        prefix = f"{emoji} " if emoji else ""
        return f'<div {common}><div class="ra-pill">{prefix}{text}</div></div>'

    if t == "word_zoom" or t == "number_pop":
        body = f'<div class="ra-bigword">{text}</div>'
        if sub:
            body += f'<div class="ra-sub" style="text-align:center;margin-top:8px">{sub}</div>'
        return f'<div {common}><div style="display:flex;flex-direction:column;align-items:center">{body}</div></div>'

    if t == "lower_third":
        return (
            f'<div {common}><div class="ra-lt">'
            f'<div class="ra-lt-name">{text}</div>'
            + (f'<div class="ra-lt-role">{sub}</div>' if sub else "")
            + "</div></div>"
        )

    if t == "emoji_burst":
        return f'<div {common}><div class="ra-emoji">{emoji or "✨"}</div></div>'

    if t == "arrow_highlight":
        return (
            f'<div {common}><div class="ra-arrow-row">'
            f'<div class="ra-arrow"></div>'
            f'<div class="ra-pill" style="background:none;box-shadow:none;padding:0">{text}</div>'
            "</div></div>"
        )

    return f'<div {common}><div class="ra-pill">{text}</div></div>'


# --- per-type GSAP tween snippets -----------------------------------------

JS_TWEENS = """
      // Reels animations — each .reels-anim gets a tween based on its type.
      for (const el of document.querySelectorAll(".reels-anim")) {
        const start = parseFloat(el.dataset.start);
        const dur = parseFloat(el.dataset.duration);
        const t = el.dataset.animType;
        const out = Math.max(0.3, dur - 0.35);
        if (t === "hook_card" || t === "cta_end") {
          tl.fromTo(el, { opacity: 0, scale: 0.92, y: 24 },
                        { opacity: 1, scale: 1, y: 0, duration: 0.45, ease: "back.out(1.5)" }, start);
          tl.to(el, { opacity: 0, scale: 0.96, duration: 0.35, ease: "power2.in" }, start + out);
        } else if (t === "text_callout") {
          tl.fromTo(el, { opacity: 0, y: 28, x: -10 },
                        { opacity: 1, y: 0, x: 0, duration: 0.4, ease: "back.out(1.7)" }, start);
          tl.to(el, { opacity: 0, y: 28, duration: 0.35, ease: "power2.in" }, start + out);
        } else if (t === "word_zoom" || t === "number_pop") {
          tl.fromTo(el, { opacity: 0, scale: 0.4 },
                        { opacity: 1, scale: 1, duration: 0.45, ease: "back.out(2.2)" }, start);
          tl.to(el, { scale: 1.06, duration: 0.25, ease: "power2.inOut", yoyo: true, repeat: 1 }, start + 0.5);
          tl.to(el, { opacity: 0, scale: 0.6, duration: 0.3, ease: "power2.in" }, start + out);
        } else if (t === "lower_third") {
          tl.fromTo(el, { opacity: 0, x: -36 },
                        { opacity: 1, x: 0, duration: 0.45, ease: "power3.out" }, start);
          tl.to(el, { opacity: 0, x: -36, duration: 0.35, ease: "power2.in" }, start + out);
        } else if (t === "emoji_burst") {
          tl.fromTo(el, { opacity: 0, scale: 0.2, rotate: -22 },
                        { opacity: 1, scale: 1, rotate: 0, duration: 0.4, ease: "back.out(2.5)" }, start);
          tl.to(el, { rotate: 8, duration: 0.18, yoyo: true, repeat: 3, ease: "sine.inOut" }, start + 0.4);
          tl.to(el, { opacity: 0, scale: 0.6, duration: 0.3, ease: "power2.in" }, start + out);
        } else if (t === "arrow_highlight") {
          tl.fromTo(el, { opacity: 0, x: 24 },
                        { opacity: 1, x: 0, duration: 0.35, ease: "power3.out" }, start);
          tl.to(el, { opacity: 0, duration: 0.35, ease: "power2.in" }, start + out);
        } else {
          tl.fromTo(el, { opacity: 0 }, { opacity: 1, duration: 0.35 }, start);
          tl.to(el, { opacity: 0, duration: 0.3, ease: "power2.in" }, start + out);
        }
      }
"""


def build_animations_payload(
    brand: BrandBook,
    animations: list[dict],
    width: int,
    intro_offset: float = 0.0,
) -> dict[str, str]:
    """Return {'css': ..., 'html': ..., 'js': ...} ready for the composer.

    `intro_offset` is added to each animation's start so they align with the
    main video segment when the composition has an intro card.
    """
    css = shared_css(brand, width)
    parts = []
    for i, a in enumerate(animations):
        shifted = dict(a)
        shifted["start"] = float(a["start"]) + intro_offset
        parts.append(build_animation_html(shifted, i))
    return {"css": css, "html": "\n      ".join(parts), "js": JS_TWEENS}
