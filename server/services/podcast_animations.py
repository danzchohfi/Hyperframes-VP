"""Auto-generate the reels_animations layer from podcast metadata.

The composer already supports `reels_animations.json` as an animation
overlay layer (text callouts, hook cards, word zooms, etc.). Today
those animations only get populated when a user manually clicks
"Sugerir animações" in the Reels card — podcast renders ship with
zero overlays, which makes the output look very plain.

This module reuses what's already in the project (transcript,
chapters, soundbites, questions, brand) and emits a sensible default
animation track without any LLM calls:

  * hook_card  at ~0.5s with brand.intro_title (or brand.name) + tagline
  * text_callout for each chapter start, with the chapter name
  * word_zoom for each soundbite, with the topic name
  * cta_end at -3s with brand.outro_text (or a sensible fallback)
  * emoji_burst for any question with a "?" — only the first 3, so
    we don't spam reactions

All outputs go through `reels_animations.normalize_animation` so they
respect the existing UI schema and the composer's renderer doesn't
need any code changes.
"""

from __future__ import annotations

from typing import Any

from . import reels_animations as ra
from .brand import BrandBook


def _truncate(text: str, n: int = 60) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def auto_generate(
    *,
    source_duration: float,
    transcript: dict[str, Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
    soundbites: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    brand: BrandBook | None = None,
    max_animations: int = 16,
) -> dict[str, Any]:
    """Return a `{animations: [...]}` payload ready to be saved as
    `reels_animations.json`. Each item is normalized via
    `reels_animations.normalize_animation`.
    """
    brand = brand or BrandBook()
    anims: list[dict[str, Any]] = []

    # 1. Hook card — only if we have a brand title/name to show
    intro_text = (brand.intro_title or brand.name or "").strip()
    if intro_text and intro_text.lower() != "brand":
        anims.append({
            "type": "hook_card",
            "start": 0.4,
            "duration": 1.6,
            "text": _truncate(intro_text, 40),
            "sub": _truncate(brand.tagline or brand.intro_subtitle or "", 60) or None,
            "variant": "gradient",
            "show_logo": True,
        })

    # 2. Chapter callouts — sticker pill at each chapter boundary, but
    # skip the very first one when there's a hook_card playing in the
    # same window (they'd visually fight for attention).
    if chapters:
        hook_end = 2.5 if anims and anims[0]["type"] == "hook_card" else 0.0
        for ch in chapters:
            try:
                start = float(ch.get("start") or 0.0)
            except (TypeError, ValueError):
                continue
            title = _truncate(str(ch.get("name") or ch.get("title") or ""), 32)
            if not title:
                continue
            place_at = start + 0.4
            if place_at < hook_end:
                continue
            anims.append({
                "type": "text_callout",
                "start": place_at,
                "duration": 1.6,
                "text": title,
                "emoji": None,
                "anchor": "bottom-left",
                "variant": "pill",
            })

    # 3. Soundbites → word_zoom highlighting the topic
    if soundbites:
        # Limit so a 1 h podcast with 30 soundbites doesn't drown the
        # screen in zooms. Top-scored ones win.
        ranked = sorted(
            soundbites,
            key=lambda s: float(s.get("score") or 0.0),
            reverse=True,
        )[:6]
        for sb in ranked:
            try:
                start = float(sb.get("start") or 0.0)
            except (TypeError, ValueError):
                continue
            text = _truncate(
                str(sb.get("topic") or sb.get("summary") or sb.get("text") or ""), 28
            )
            if not text:
                continue
            anims.append({
                "type": "word_zoom",
                "start": max(0.0, start - 0.1),
                "duration": 0.9,
                "text": text,
                "variant": "accent",
            })

    # 4. First 3 questions → emoji_burst (reaction beat)
    if questions:
        for q in questions[:3]:
            try:
                start = float(q.get("start") or 0.0)
            except (TypeError, ValueError):
                continue
            anims.append({
                "type": "emoji_burst",
                "start": start,
                "duration": 1.0,
                "emoji": "💡",
                "anchor": "center-right",
            })

    # 5. CTA at the end
    cta_text = (brand.outro_text or "").strip()
    if not cta_text and intro_text and intro_text.lower() != "brand":
        cta_text = f"Continua em {intro_text}"
    if cta_text and source_duration > 5.0:
        anims.append({
            "type": "cta_end",
            "start": max(0.0, source_duration - 3.5),
            "duration": 2.8,
            "text": _truncate(cta_text, 48),
            "sub": None,
            "variant": "gradient",
            "show_logo": True,
        })

    # Normalize + clamp to source duration; drop any whose normalized
    # form ends up empty (no text after trimming, etc.).
    normalized: list[dict[str, Any]] = []
    for a in anims:
        n = ra.normalize_animation(a, source_duration=source_duration)
        if n.get("type") in ("emoji_burst",) and not n.get("emoji"):
            continue
        if n.get("type") not in ("emoji_burst", "arrow_highlight") and not n.get("text"):
            continue
        normalized.append(n)

    # Sort by start so the renderer processes them in order, and cap.
    normalized.sort(key=lambda a: float(a.get("start") or 0.0))
    if len(normalized) > max_animations:
        # Always keep the hook + cta if present, then evenly drop middle ones.
        head = [a for a in normalized if a["type"] == "hook_card"]
        tail = [a for a in normalized if a["type"] == "cta_end"]
        body = [a for a in normalized if a["type"] not in ("hook_card", "cta_end")]
        keep = max_animations - len(head) - len(tail)
        if keep > 0 and body:
            step = max(1, len(body) // keep)
            body = body[::step][:keep]
        normalized = head + body + tail
        normalized.sort(key=lambda a: float(a.get("start") or 0.0))

    return {
        "source": "auto",
        "source_duration": source_duration,
        "animations": normalized,
        "count": len(normalized),
    }
