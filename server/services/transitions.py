"""Cinematic transitions between chapter / soundbite boundaries.

Each transition is a self-contained overlay (a single absolutely-
positioned `<div>` per boundary) plus a tiny GSAP tween. The composer
calls `build_transitions_payload(kind, boundaries, intro_offset)` and
gets `{css, html, js}` ready to splice into the composition.

Five built-in kinds:
- "whip"     — directional blur + scale + slight rotate (0.4s).
- "flash"    — white frame fade through (0.2s).
- "glitch"   — rgb-split + skew jitter, MotionVFX classic (0.3s).
- "dissolve" — soft cross-fade with motion blur (0.6s).
- "none"     — disabled (returns empty payload).

A boundary is just a float (seconds, in the final composition's
timeline — caller already accounts for intro_offset). Transitions
shorter than 0.3s from t=0 are skipped (no effect at the very start).
"""

from __future__ import annotations

TRANSITION_KINDS = ("whip", "flash", "glitch", "dissolve")


# Per-kind CSS — uses class hooks like `.trans.kind-whip` so a single
# stylesheet covers all kinds even when only one is active.
TRANSITION_CSS = """
      .cinematic-transition {
        position: absolute; inset: 0;
        pointer-events: none; z-index: 76;
        opacity: 0;
        will-change: opacity, transform, filter;
      }
      /* WHIP — directional blur sweep, like a fast camera pan. */
      .cinematic-transition.kind-whip {
        background:
          linear-gradient(90deg,
            rgba(0,0,0,0.0) 0%,
            rgba(0,0,0,0.45) 40%,
            rgba(0,0,0,0.85) 50%,
            rgba(0,0,0,0.45) 60%,
            rgba(0,0,0,0.0) 100%);
        transform: translateX(-100%) scaleX(1.4);
        filter: blur(28px);
      }
      /* FLASH — pure white frame. */
      .cinematic-transition.kind-flash {
        background: #ffffff;
      }
      /* GLITCH — colorful split bands, rendered via stacked gradients. */
      .cinematic-transition.kind-glitch {
        background:
          linear-gradient(180deg,
            rgba(255, 60, 60, 0.0) 0%,
            rgba(255, 60, 60, 0.35) 12%,
            rgba(255, 60, 60, 0.0) 24%,
            rgba(60, 255, 220, 0.0) 50%,
            rgba(60, 255, 220, 0.35) 62%,
            rgba(60, 255, 220, 0.0) 74%);
        mix-blend-mode: screen;
        filter: blur(2px);
      }
      /* DISSOLVE — soft black scrim that fades through, paired with a
         brief motion blur on the underlying video (applied via JS class
         on the root). */
      .cinematic-transition.kind-dissolve {
        background: rgba(0, 0, 0, 0.4);
      }
      #root.transition-dissolve-active #body-video {
        filter: blur(6px);
        transition: filter 0.2s ease-out;
      }
"""


def _whip_js(boundaries: list[float]) -> str:
    if not boundaries:
        return ""
    return """
      // WHIP transitions — fast horizontal sweep with motion blur.
      for (const t of document.querySelectorAll(".cinematic-transition.kind-whip")) {
        const start = parseFloat(t.dataset.start);
        tl.fromTo(t,
          { x: "-100%", opacity: 0, scaleX: 1.4, rotate: 0 },
          { x: "100%", opacity: 1, scaleX: 1, rotate: 1.5, duration: 0.4,
            ease: "power3.inOut" },
          start);
        tl.to(t, { opacity: 0, duration: 0.12, ease: "power2.out" }, start + 0.4);
      }
    """


def _flash_js(boundaries: list[float]) -> str:
    if not boundaries:
        return ""
    return """
      // FLASH transitions — quick white fade-through.
      for (const t of document.querySelectorAll(".cinematic-transition.kind-flash")) {
        const start = parseFloat(t.dataset.start);
        tl.fromTo(t,
          { opacity: 0 },
          { opacity: 0.95, duration: 0.08, ease: "power2.in" },
          start);
        tl.to(t, { opacity: 0, duration: 0.18, ease: "power3.out" }, start + 0.08);
      }
    """


def _glitch_js(boundaries: list[float]) -> str:
    if not boundaries:
        return ""
    return """
      // GLITCH transitions — rgb-split bands + skew jitter for 0.3s.
      for (const t of document.querySelectorAll(".cinematic-transition.kind-glitch")) {
        const start = parseFloat(t.dataset.start);
        tl.fromTo(t,
          { opacity: 0, skewX: 0, y: 0 },
          { opacity: 1, duration: 0.05, ease: "none" }, start);
        // 5-frame jitter sequence
        const jitters = [
          { skewX: 4,  y: -6, x: -8, time: 0.05 },
          { skewX: -3, y: 5,  x: 6,  time: 0.11 },
          { skewX: 2,  y: -4, x: -4, time: 0.18 },
          { skewX: -1, y: 2,  x: 3,  time: 0.24 },
          { skewX: 0,  y: 0,  x: 0,  time: 0.30 },
        ];
        for (const j of jitters) {
          tl.to(t, { skewX: j.skewX, y: j.y, x: j.x, duration: 0.05, ease: "none" },
                start + j.time);
        }
        tl.to(t, { opacity: 0, duration: 0.08, ease: "power2.out" }, start + 0.30);
      }
    """


def _dissolve_js(boundaries: list[float]) -> str:
    if not boundaries:
        return ""
    return """
      // DISSOLVE transitions — slow cross-fade with motion blur on the
      // body video. The root gets a class toggle so the CSS blur kicks
      // in only during the dissolve window.
      const root = document.getElementById("root");
      for (const t of document.querySelectorAll(".cinematic-transition.kind-dissolve")) {
        const start = parseFloat(t.dataset.start);
        tl.call(() => { if (root) root.classList.add("transition-dissolve-active"); }, [], start);
        tl.fromTo(t,
          { opacity: 0 },
          { opacity: 1, duration: 0.3, ease: "power2.inOut" },
          start);
        tl.to(t, { opacity: 0, duration: 0.3, ease: "power2.inOut" }, start + 0.3);
        tl.call(() => { if (root) root.classList.remove("transition-dissolve-active"); }, [], start + 0.6);
      }
    """


_JS_BY_KIND = {
    "whip":     _whip_js,
    "flash":    _flash_js,
    "glitch":   _glitch_js,
    "dissolve": _dissolve_js,
}

# How early before the boundary the transition fires. Most kinds peak
# at the moment of the cut; pulling the start back by half their
# duration centres the effect on the boundary.
_LEAD_BY_KIND = {
    "whip":     0.2,
    "flash":    0.1,
    "glitch":   0.15,
    "dissolve": 0.3,
}


def build_transitions_payload(
    kind: str | None,
    boundaries: list[float],
    *,
    track_index_base: int = 8,
) -> dict[str, str]:
    """Build CSS + HTML + JS for the chosen transition kind.

    `boundaries` is a list of timestamps (seconds, in the composition's
    timeline). The caller is responsible for adding any intro_offset.
    Anything before t=0.5 is dropped (no transition into the opening
    frame), and consecutive boundaries closer than 1.0s are de-duped
    (avoids stacking on top of each other).
    """
    if not kind or kind == "none" or kind not in TRANSITION_KINDS:
        return {"css": "", "html": "", "js": ""}
    if not boundaries:
        return {"css": "", "html": "", "js": ""}

    cleaned: list[float] = []
    last = -999.0
    for b in sorted(float(x) for x in boundaries):
        if b < 0.5:
            continue
        if b - last < 1.0:
            continue
        cleaned.append(b)
        last = b
    if not cleaned:
        return {"css": "", "html": "", "js": ""}

    lead = _LEAD_BY_KIND[kind]
    track_idx = track_index_base
    html_parts: list[str] = []
    for i, t in enumerate(cleaned):
        start = max(0.0, t - lead)
        html_parts.append(
            f'<div class="cinematic-transition kind-{kind} clip" '
            f'data-trans-i="{i}" '
            f'data-start="{start:.3f}" data-duration="0.8" '
            f'data-track-index="{track_idx + i}"></div>'
        )

    return {
        "css": TRANSITION_CSS,
        "html": "\n      ".join(html_parts),
        "js": _JS_BY_KIND[kind](cleaned),
    }
