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

# Each entry: {label, default_duration, default_anchor, ui_fields, variants?}
ANIMATION_TYPES: dict[str, dict[str, Any]] = {
    "hook_card": {
        "label": "Hook intro (full-screen)",
        "default_duration": 1.4,
        "default_anchor": "center",
        "fields": ["text", "sub"],
        "variants": ["bold", "gradient", "minimal", "keynote"],
    },
    "cta_end": {
        "label": "CTA final",
        "default_duration": 2.0,
        "default_anchor": "center",
        "fields": ["text", "sub"],
        "variants": ["bold", "gradient", "minimal", "keynote"],
    },
    "text_callout": {
        "label": "Callout em sticker",
        "default_duration": 1.6,
        "default_anchor": "bottom-left",
        "fields": ["text", "emoji"],
        "variants": ["pill", "block"],
    },
    "word_zoom": {
        "label": "Palavra em destaque",
        "default_duration": 0.9,
        "default_anchor": "center",
        "fields": ["text"],
        "variants": ["accent"],
    },
    "lower_third": {
        "label": "Lower-third (nome + cargo)",
        "default_duration": 3.0,
        "default_anchor": "bottom-left",
        "fields": ["text", "sub"],
        "variants": ["default"],
    },
    "emoji_burst": {
        "label": "Emoji burst",
        "default_duration": 1.0,
        "default_anchor": "center-right",
        "fields": ["emoji"],
        "variants": ["default"],
    },
    "number_pop": {
        "label": "Número grande",
        "default_duration": 1.2,
        "default_anchor": "center",
        "fields": ["text", "sub"],
        "variants": ["accent"],
    },
    "arrow_highlight": {
        "label": "Seta + destaque",
        "default_duration": 1.4,
        "default_anchor": "bottom",
        "fields": ["text", "anchor"],
        "variants": ["default"],
    },
}


# --- named easing presets --------------------------------------------------

# Apple-keynote / MotionVFX cinema-grade curves the LLM can request and
# users can override per-animation. Mapped to GSAP ease strings that
# don't require paid plugins (CustomEase). The Python side carries
# labels/descriptions for the planner system prompt; the JS side reads
# the same names off `data-easing` to override the per-type default.
EASINGS: dict[str, dict[str, str]] = {
    "apple-emphasis": {
        "gsap": "power3.out",
        "label": "Apple Emphasis",
        "description": "Soft snap, Apple keynote entrance.",
    },
    "apple-decel": {
        "gsap": "expo.out",
        "label": "Apple Decel",
        "description": "Slow-out, Pages/Numbers reveal feel.",
    },
    "cinema-punch": {
        "gsap": "back.out(3.5)",
        "label": "Cinema Punch",
        "description": "MotionVFX impact, overshoot then settle.",
    },
    "swift-release": {
        "gsap": "power4.out",
        "label": "Swift Release",
        "description": "Fast in, slow settle.",
    },
    "glide-in": {
        "gsap": "expo.out",
        "label": "Glide In",
        "description": "Smooth hero text reveal.",
    },
}

# When an animation doesn't specify an easing explicitly we use the
# type's default. None means "fall back to the legacy hard-coded ease".
DEFAULT_EASING_FOR_TYPE: dict[str, str | None] = {
    "hook_card":      "apple-emphasis",
    "cta_end":        "apple-decel",
    "text_callout":   None,
    "word_zoom":      "cinema-punch",
    "number_pop":     "cinema-punch",
    "lower_third":    "swift-release",
    "emoji_burst":    None,
    "arrow_highlight": None,
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
    # variant — clamp to the supported list, fall back to the first variant
    variants = spec.get("variants") or ["default"]
    requested = (a.get("variant") or variants[0]).strip()
    a["variant"] = requested if requested in variants else variants[0]
    # Named easing — clamped to EASINGS keys. Falls back to the type's
    # default; the JS resolver then maps the name to a GSAP ease string.
    raw_ease = (a.get("easing") or "").strip().lower() or None
    if raw_ease in EASINGS:
        a["easing"] = raw_ease
    else:
        a["easing"] = DEFAULT_EASING_FOR_TYPE.get(t)
    # show_logo only applies to hook_card / cta_end; default True so users
    # don't have to set it explicitly. The composer/preview drops it when
    # there's no brand logo, so it's safe to leave on.
    a["show_logo"] = bool(a.get("show_logo", True)) if t in ("hook_card", "cta_end") else False
    # Custom HTML / CSS only on hook_card / cta_end, sanitized aggressively.
    # The composer reads these to replace the templated card body when
    # present; everything else stays the same (entrance/exit tween,
    # positioning, optional logo).
    if t in ("hook_card", "cta_end"):
        from . import html_sanitize as _hs
        raw_html = (a.get("custom_html") or "").strip()
        raw_css = (a.get("custom_css") or "").strip()
        a["custom_html"] = _hs.sanitize_html(raw_html, max_chars=4000) if raw_html else None
        a["custom_css"] = _hs.sanitize_css(raw_css, max_chars=1500) if raw_css else None
        # If sanitization stripped everything (e.g. the LLM emitted only a
        # <script>), drop the field rather than emit an empty card body.
        if not a["custom_html"]:
            a["custom_html"] = None
            a["custom_css"] = None
    else:
        a["custom_html"] = None
        a["custom_css"] = None
    # Sound: when the user (or the type's default) provides an SFX preset
    # name, render passes will mix it at the animation's start time.
    # `sfx` of None or "" silences this animation; "default" picks the
    # type's recommended preset.
    sfx = a.get("sfx")
    if sfx == "default":
        # Resolved lazily inside renderers so we don't import sfx_lib here
        # (composer ships without it for renders that don't use sound).
        a["sfx"] = "_default"
    elif sfx in (None, "", "none", "off"):
        a["sfx"] = None
    else:
        a["sfx"] = str(sfx).strip() or None
    try:
        vol = float(a.get("sfx_volume") if a.get("sfx_volume") is not None else 0.7)
    except (TypeError, ValueError):
        vol = 0.7
    a["sfx_volume"] = max(0.0, min(1.5, vol))
    # TTS narration. Only hook_card / cta_end carry it (the others get
    # cluttered with voice). Empty string means "no narration".
    if t in ("hook_card", "cta_end"):
        a["tts"] = (a.get("tts") or "").strip() or None
        a["tts_voice"] = (a.get("tts_voice") or "alloy").strip() or "alloy"
        try:
            a["tts_volume"] = max(0.0, min(1.5, float(a.get("tts_volume") if a.get("tts_volume") is not None else 1.0)))
        except (TypeError, ValueError):
            a["tts_volume"] = 1.0
    else:
        a["tts"] = None
        a["tts_voice"] = None
        a["tts_volume"] = 0.0
    return a


def resolve_default_sfx(anim: dict) -> str | None:
    """Translate sfx='_default' (sentinel from normalize_animation) into a
    real preset name based on the animation type. Renderers call this
    just before building their audio mix."""
    if anim.get("sfx") != "_default":
        return anim.get("sfx")
    from . import sfx_lib  # local import to avoid cycles
    return sfx_lib.DEFAULT_SFX_FOR_TYPE.get(anim["type"])


# --- shared CSS (anchors / palette tokens) ---------------------------------

def shared_css(brand: BrandBook, width: int) -> str:
    p = brand.palette
    big = int(width * 0.085)
    med = int(width * 0.05)
    sml = int(width * 0.024)
    logo_size = int(width * 0.10)
    return f"""
      .reels-anim {{ position: absolute; z-index: 80; pointer-events: none; }}
      /* Depth: hook / CTA cards get a perspective so the entrance can
         pitch in from behind the camera. Other anims are flat. */
      .reels-anim[data-anim-type="hook_card"],
      .reels-anim[data-anim-type="cta_end"] {{
        perspective: 1200px;
        transform-style: preserve-3d;
      }}
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
      .reels-anim .ra-pill-block {{
        background: {p.background}ee;
        border: 1px solid {p.accent}66;
        border-left: 4px solid {p.accent};
        border-radius: 10px;
        padding: 14px 20px;
        max-width: 70%;
        text-align: left;
      }}
      .reels-anim .ra-pill-block .ra-title {{
        font-size: {int(sml * 1.1)}px;
        font-weight: 800;
        color: {p.foreground};
      }}
      /* Hook / CTA — three visual variants */
      .reels-anim .ra-card {{
        padding: 28px 36px;
        border-radius: 18px;
        text-align: center;
        display: flex; flex-direction: column; align-items: center; gap: 14px;
      }}
      .reels-anim .ra-card.variant-bold {{
        background: linear-gradient(160deg, {p.background}dd, {p.background}99);
        border: 1px solid {p.primary}55;
        backdrop-filter: blur(14px);
        box-shadow: 0 24px 60px rgba(0,0,0,0.55);
      }}
      .reels-anim .ra-card.variant-gradient {{
        position: absolute; inset: 0;
        padding: 0; border-radius: 0;
        justify-content: center;
        background: linear-gradient(135deg, {p.primary} 0%, {p.accent} 60%, {p.secondary} 100%);
        box-shadow: inset 0 -120px 200px rgba(0,0,0,0.35);
      }}
      .reels-anim .ra-card.variant-minimal {{
        background: transparent;
        text-shadow: 0 4px 28px rgba(0,0,0,0.7), 0 2px 6px rgba(0,0,0,0.5);
      }}
      /* Keynote variant — Apple-style cinematic typography. The
         entrance JS wraps each character in <span class="char"> and
         staggers them; this CSS sets up the per-character canvas and
         a layered drop-shadow for the "cone of light" feel. */
      .reels-anim .ra-card.variant-keynote {{
        background: transparent;
        padding: 24px 32px;
        gap: 18px;
        filter:
          drop-shadow(0 12px 36px rgba(0, 0, 0, 0.55))
          drop-shadow(0 2px 6px rgba(0, 0, 0, 0.4));
      }}
      .reels-anim .ra-card.variant-keynote .ra-title {{
        font-size: {int(med * 1.25)}px;
        font-weight: 600;
        letter-spacing: -0.025em;
        line-height: 1.02;
        font-variation-settings: 'wght' 600;
      }}
      .reels-anim .ra-card.variant-keynote .ra-sub {{
        font-size: {int(sml * 1.05)}px;
        font-weight: 400;
        color: {p.foreground}c8;
        letter-spacing: -0.012em;
      }}
      .reels-anim .ra-card.variant-keynote .char {{
        display: inline-block;
        transform-origin: 50% 100%;
        will-change: transform, opacity, filter;
        white-space: pre;
      }}
      .reels-anim .ra-card.variant-keynote .ra-logo {{
        transform: translateZ(20px);
      }}
      /* Custom-HTML hook/CTA cards: strip the templated container styling
         so the LLM's CSS is the source of truth. The OUTER positioning
         (anchor-*) and entrance/exit tween still apply, but everything
         inside .ra-custom-body is the LLM's canvas. */
      .reels-anim .ra-card.ra-card-custom {{
        padding: 0;
        gap: 0;
        background: transparent;
        border: 0;
        box-shadow: none;
        backdrop-filter: none;
        align-items: stretch;
        justify-content: stretch;
      }}
      .reels-anim .ra-card.ra-card-custom.variant-gradient {{
        position: absolute; inset: 0;
      }}
      .reels-anim .ra-card-custom .ra-custom-body {{
        color: {p.foreground};
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      }}
      .reels-anim .ra-card .ra-title {{
        font-size: {med}px;
        font-weight: 800;
        color: {p.foreground};
        letter-spacing: -0.025em;
        line-height: 1.05;
      }}
      .reels-anim .ra-card.variant-gradient .ra-title {{
        font-size: {int(med * 1.3)}px;
        text-transform: uppercase;
      }}
      .reels-anim .ra-card.variant-minimal .ra-title {{
        font-size: {int(med * 1.2)}px;
      }}
      .reels-anim .ra-card .ra-sub {{
        font-size: {sml}px;
        font-weight: 500;
        color: {p.foreground}b0;
        margin-top: 4px;
      }}
      .reels-anim .ra-card.variant-gradient .ra-sub {{ color: {p.foreground}; }}
      .reels-anim .ra-logo {{
        width: {logo_size}px; height: auto; max-height: {logo_size}px;
        object-fit: contain;
        filter: drop-shadow(0 4px 16px rgba(0,0,0,0.4));
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


def _split_chars(text: str) -> str:
    """Wrap each char of `text` in <span class="char"> for per-character
    animation. Spaces are kept as-is inside the span (with `white-space:
    pre`) so word-wrapping at the line level still works."""
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        out.append(f'<span class="char">{html.escape(ch)}</span>')
    return "".join(out)


def build_animation_html(anim: dict, index: int, logo_src: str | None = None) -> str:
    """Return a single <div class='reels-anim clip ...'> for the animation."""
    t = anim["type"]
    aid = f"ra-{index}"
    cls = f"reels-anim clip anchor-{anim.get('anchor', 'center')}"
    easing_attr = f' data-easing="{anim["easing"]}"' if anim.get("easing") else ""
    variant_attr = f' data-variant="{_safe(anim.get("variant") or "")}"'
    common = (
        f'id="{aid}" class="{cls}" '
        f'data-start="{anim["start"]:.3f}" '
        f'data-duration="{anim["duration"]:.3f}" '
        f'data-track-index="{20 + index}" '
        f'data-anim-type="{t}"'
        f'{variant_attr}{easing_attr}'
    )
    text = _safe(anim.get("text") or "")
    sub = _safe(anim.get("sub") or "")
    emoji = _safe(anim.get("emoji") or "")
    variant = anim.get("variant") or "bold"

    if t == "hook_card" or t == "cta_end":
        show_logo = anim.get("show_logo") and logo_src
        logo_html = (
            f'<img class="ra-logo" src="{_safe(logo_src)}" alt="" />'
            if show_logo else ""
        )
        # Custom body: sanitized HTML + scoped CSS replace the templated
        # .ra-title / .ra-sub structure. The logo (if any) still floats
        # at the top of the card so brand presence is preserved across
        # custom looks — pull it out of the custom area.
        custom_html = anim.get("custom_html")
        custom_css = anim.get("custom_css")
        if custom_html:
            style_block = ""
            if custom_css:
                from . import html_sanitize as _hs
                scoped = _hs.scope_css(custom_css, aid)
                if scoped:
                    style_block = f'<style>{scoped}</style>'
            body = f'<div class="ra-card ra-card-custom variant-{_safe(variant)}">'
            body += style_block
            if logo_html:
                body += logo_html
            body += f'<div class="ra-custom-body">{custom_html}</div>'
            body += "</div>"
            return f'<div {common}>{body}</div>'
        body = f'<div class="ra-card variant-{_safe(variant)}">'
        if logo_html:
            body += logo_html
        if variant == "keynote":
            # Per-character spans so the JS can stagger blur/y/opacity.
            # The raw `text` field was already escaped above, so we
            # split from the original anim text (still safe via
            # _split_chars's html.escape).
            title_html = _split_chars(anim.get("text") or "")
            sub_html = _split_chars(anim.get("sub") or "") if anim.get("sub") else ""
            body += f'<div class="ra-title">{title_html}</div>'
            if sub_html:
                body += f'<div class="ra-sub">{sub_html}</div>'
        else:
            body += f'<div class="ra-title">{text}</div>'
            if sub:
                body += f'<div class="ra-sub">{sub}</div>'
        body += "</div>"
        return f'<div {common}>{body}</div>'

    if t == "text_callout":
        prefix = f"{emoji} " if emoji else ""
        if variant == "block":
            return (
                f'<div {common}><div class="ra-pill-block">'
                f'<div class="ra-title">{prefix}{text}</div>'
                + (f'<div class="ra-sub" style="color:inherit;opacity:0.75">{sub}</div>' if sub else "")
                + "</div></div>"
            )
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
      // Apple/MotionVFX-inspired named eases. Keep in sync with
      // server/services/reels_animations.py EASINGS map. All values
      // resolve to stock GSAP eases so no CustomEase plugin is needed.
      const REELS_EASINGS = {
        "apple-emphasis": "power3.out",
        "apple-decel":    "expo.out",
        "cinema-punch":   "back.out(3.5)",
        "swift-release":  "power4.out",
        "glide-in":       "expo.out",
      };
      function pickEase(el, fallback) {
        const name = el.dataset.easing;
        return (name && REELS_EASINGS[name]) || fallback;
      }
      // Reels animations — each .reels-anim gets a tween based on its type.
      for (const el of document.querySelectorAll(".reels-anim")) {
        const start = parseFloat(el.dataset.start);
        const dur = parseFloat(el.dataset.duration);
        const t = el.dataset.animType;
        const variant = el.dataset.variant || "";
        const out = Math.max(0.3, dur - 0.35);
        if (t === "hook_card" || t === "cta_end") {
          const card = el.querySelector(".ra-card") || el;
          const chars = variant === "keynote"
            ? el.querySelectorAll(".ra-card.variant-keynote .char")
            : null;
          if (chars && chars.length) {
            // Apple keynote feel: card itself glides in with depth, then
            // each char emerges from blur with a tight stagger.
            const ease = pickEase(el, "expo.out");
            tl.fromTo(card,
              { opacity: 0, rotateX: -8, z: -40 },
              { opacity: 1, rotateX: 0, z: 0, duration: 0.55, ease: ease },
              start);
            tl.fromTo(chars,
              { opacity: 0, y: 24, filter: "blur(12px)" },
              { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.5,
                ease: ease, stagger: 0.035 },
              start + 0.05);
            tl.to(el, { opacity: 0, scale: 0.97, duration: 0.4,
                        ease: "power2.in" }, start + out);
          } else {
            // Default hook/CTA — depth-aware entrance with named ease.
            const ease = pickEase(el, "back.out(1.5)");
            tl.fromTo(el,
              { opacity: 0, scale: 0.92, y: 24, rotateX: -4 },
              { opacity: 1, scale: 1, y: 0, rotateX: 0,
                duration: 0.5, ease: ease },
              start);
            tl.to(el, { opacity: 0, scale: 0.96, duration: 0.35,
                        ease: "power2.in" }, start + out);
          }
        } else if (t === "text_callout") {
          tl.fromTo(el, { opacity: 0, y: 28, x: -10 },
                        { opacity: 1, y: 0, x: 0, duration: 0.4, ease: "back.out(1.7)" }, start);
          tl.to(el, { opacity: 0, y: 28, duration: 0.35, ease: "power2.in" }, start + out);
        } else if (t === "word_zoom" || t === "number_pop") {
          const ease = pickEase(el, "back.out(2.2)");
          tl.fromTo(el, { opacity: 0, scale: 0.4 },
                        { opacity: 1, scale: 1, duration: 0.45, ease: ease }, start);
          tl.to(el, { scale: 1.06, duration: 0.25, ease: "power2.inOut", yoyo: true, repeat: 1 }, start + 0.5);
          tl.to(el, { opacity: 0, scale: 0.6, duration: 0.3, ease: "power2.in" }, start + out);
        } else if (t === "lower_third") {
          const ease = pickEase(el, "power3.out");
          tl.fromTo(el, { opacity: 0, x: -36 },
                        { opacity: 1, x: 0, duration: 0.45, ease: ease }, start);
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
    logo_src: str | None = None,
) -> dict[str, str]:
    """Return {'css': ..., 'html': ..., 'js': ...} ready for the composer.

    `intro_offset` is added to each animation's start so they align with the
    main video segment when the composition has an intro card.
    `logo_src` is the URL/path the hook_card / cta_end variants should use
    when their show_logo flag is on.
    """
    css = shared_css(brand, width)
    parts = []
    for i, a in enumerate(animations):
        shifted = dict(a)
        shifted["start"] = float(a["start"]) + intro_offset
        parts.append(build_animation_html(shifted, i, logo_src=logo_src))
    return {"css": css, "html": "\n      ".join(parts), "js": JS_TWEENS}
