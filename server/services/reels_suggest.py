"""Suggest where to drop reel animations based on transcript + story + brand.

Two layers:
- Pure heuristics (no API call): always fire.
- Optional LLM pass: refines/expands the list based on the script meaning.

Heuristics:
- hook_card at the start (uses brand.intro_title or the first sentence).
- cta_end at the end (uses brand.outro_text or "Siga @{brand}").
- text_callout at each topic transition from story.json.
- number_pop on big numbers or percentages.
- word_zoom on emphasis words (curated PT + EN lists).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import AsyncOpenAI

from .brand import BrandBook


# Words that almost always carry emphasis in reels — punchy, single-word
# beats. Kept short on purpose so word_zoom doesn't fire constantly.
EMPHASIS_WORDS_PT = {
    "nunca", "sempre", "melhor", "pior", "incrível", "incrivel", "tudo",
    "nada", "todo", "todos", "todas", "exato", "errado", "certo", "agora",
    "hoje", "primeira", "primeiro", "principal", "segredo", "verdade",
}
EMPHASIS_WORDS_EN = {
    "never", "always", "best", "worst", "amazing", "everything", "nothing",
    "everyone", "exactly", "wrong", "right", "now", "today", "first", "main",
    "secret", "truth", "biggest", "huge",
}
_NUMBER_PATTERNS = [
    re.compile(r"^R\$\s?\d", re.IGNORECASE),
    re.compile(r"\d{2,}%?$"),
    re.compile(r"\d+x$", re.IGNORECASE),
    re.compile(r"\$\d"),
]


def _is_emphasis(word: str) -> bool:
    w = (word or "").strip().lower().strip(".,!?;:")
    return w in EMPHASIS_WORDS_PT or w in EMPHASIS_WORDS_EN


def _is_number(word: str) -> bool:
    w = (word or "").strip().strip(".,!?;:")
    if not w:
        return False
    if any(p.match(w) for p in _NUMBER_PATTERNS):
        return True
    # Bare integers >= 2 digits, or "20%", "5x".
    stripped = w.replace(",", "").replace(".", "")
    return stripped.isdigit() and len(stripped) >= 2


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def suggest_heuristic(
    *,
    transcript: dict | None,
    story: dict | None,
    brand: BrandBook,
    duration: float,
    max_animations: int = 14,
) -> list[dict]:
    """Pure-heuristic suggestions. No API calls."""
    animations: list[dict] = []
    intro_title = (brand.intro_title or brand.name or "").strip()
    outro_text = (brand.outro_text or f"Siga @{(brand.name or 'voce').lower()}").strip()

    # Hook at start
    if duration > 1.0:
        first_sentence = ""
        if transcript and transcript.get("text"):
            first_sentence = _truncate((transcript["text"] or "").split(".")[0], 48)
        animations.append({
            "start": 0.0,
            "duration": 1.4,
            "type": "hook_card",
            "text": intro_title or first_sentence or "Watch this",
            "sub": brand.tagline or first_sentence if intro_title else None,
            "anchor": "center",
            "source": "heuristic:start",
        })

    # CTA at end
    if duration > 4.0:
        animations.append({
            "start": max(0.0, duration - 2.2),
            "duration": 2.0,
            "type": "cta_end",
            "text": outro_text,
            "sub": brand.tagline if brand.tagline and brand.tagline != outro_text else None,
            "anchor": "center",
            "source": "heuristic:end",
        })

    # Topic transitions from story
    if story and story.get("chapters"):
        for ch in story["chapters"]:
            start = ch.get("start") or ch.get("time")
            if start is None:
                continue
            name = (ch.get("name") or ch.get("title") or "").strip()
            if not name:
                continue
            animations.append({
                "start": float(start),
                "duration": 1.5,
                "type": "text_callout",
                "text": _truncate(name, 36),
                "anchor": "bottom-left",
                "source": "heuristic:chapter",
            })

    # Number pops + word zooms from transcript words
    if transcript and transcript.get("words"):
        words = transcript["words"]
        last_pop_at = -2.0   # debounce so we don't spam the timeline
        last_zoom_at = -2.0
        for w in words:
            try:
                ws = float(w.get("start") or 0.0)
                we = float(w.get("end") or ws + 0.3)
            except (TypeError, ValueError):
                continue
            text = (w.get("word") or "").strip()
            if not text:
                continue
            if _is_number(text) and ws - last_pop_at >= 1.5:
                animations.append({
                    "start": max(0.0, ws - 0.05),
                    "duration": max(0.9, we - ws + 0.7),
                    "type": "number_pop",
                    "text": text.strip(".,!?;:"),
                    "anchor": "center",
                    "source": "heuristic:number",
                })
                last_pop_at = ws
            elif _is_emphasis(text) and ws - last_zoom_at >= 4.0:
                animations.append({
                    "start": max(0.0, ws - 0.05),
                    "duration": 0.8,
                    "type": "word_zoom",
                    "text": text.strip(".,!?;:").upper(),
                    "anchor": "center",
                    "source": "heuristic:emphasis",
                })
                last_zoom_at = ws

    # Cap + sort by start
    animations.sort(key=lambda a: a["start"])
    if len(animations) > max_animations:
        # Keep hook + cta + most-spread interior picks
        head = animations[0:1]
        tail = animations[-1:] if animations[-1]["source"] == "heuristic:end" else []
        body = animations[1:-1] if tail else animations[1:]
        # Even-stride sample
        keep = max_animations - len(head) - len(tail)
        if keep < len(body):
            stride = len(body) / max(keep, 1)
            body = [body[int(i * stride)] for i in range(keep)]
        animations = head + body + tail

    return animations


_LLM_SYSTEM = """You design reel motion graphics. Given a transcript and an
optional story breakdown, return a JSON list of animation cues that should
appear over the video. Each cue must be:
  {"start": float seconds, "duration": float seconds, "type": one of
   ["hook_card","cta_end","text_callout","word_zoom","lower_third",
    "emoji_burst","number_pop","arrow_highlight"],
   "text": short string, "sub": optional string, "emoji": optional 1-3 chars,
   "reason": one-sentence why}

Rules:
- The very first animation MUST be a hook_card at start=0.0 with duration 1.3-1.8.
- The very last animation MUST be a cta_end ending at the video duration.
- 6–10 animations total. Spread them out — no two animations should overlap.
- Animation text should be 1-6 words, punchy. Use the LANGUAGE of the transcript.
- Numbers, percentages, brand names, and emphasis words deserve word_zoom or number_pop.
- Topic transitions (from the chapters list, if provided) deserve text_callout.

Return JSON: {"animations": [...]}"""


async def suggest_with_llm(
    *,
    transcript: dict | None,
    story: dict | None,
    brand: BrandBook,
    duration: float,
    max_animations: int = 10,
) -> list[dict]:
    """LLM-driven suggestions. Falls back to heuristics on any failure."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not transcript:
        return suggest_heuristic(
            transcript=transcript, story=story, brand=brand,
            duration=duration, max_animations=max_animations,
        )

    text = (transcript.get("text") or "").strip()
    if not text:
        return suggest_heuristic(
            transcript=transcript, story=story, brand=brand,
            duration=duration, max_animations=max_animations,
        )

    chapters_text = ""
    if story and story.get("chapters"):
        rows = [
            f"- {c.get('start', 0):.1f}s: {(c.get('name') or '').strip()}"
            for c in story["chapters"] if c.get("name")
        ]
        if rows:
            chapters_text = "Story chapters:\n" + "\n".join(rows) + "\n\n"

    user_msg = (
        f"Brand: {brand.name or 'Brand'}"
        + (f" — {brand.tagline}" if brand.tagline else "")
        + f"\nIntro title: {brand.intro_title or '(none)'}"
        + f"\nOutro text: {brand.outro_text or '(none)'}"
        + f"\nDuration: {duration:.1f}s. Max animations: {max_animations}.\n\n"
        + chapters_text
        + f"Transcript:\n{_truncate(text, 3500)}"
    )

    try:
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.5,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        data: dict[str, Any] = json.loads(resp.choices[0].message.content or "{}")
        raw = data.get("animations") or []
    except Exception:
        raw = []

    if not raw:
        return suggest_heuristic(
            transcript=transcript, story=story, brand=brand,
            duration=duration, max_animations=max_animations,
        )

    cleaned: list[dict] = []
    for r in raw:
        try:
            s = float(r.get("start") or 0.0)
            d = float(r.get("duration") or 1.5)
        except (TypeError, ValueError):
            continue
        if s >= duration:
            continue
        d = max(0.4, min(d, duration - s))
        cleaned.append({
            "start": round(s, 3),
            "duration": round(d, 3),
            "type": (r.get("type") or "text_callout"),
            "text": (r.get("text") or "").strip(),
            "sub": (r.get("sub") or None) and r["sub"].strip(),
            "emoji": (r.get("emoji") or None) and r["emoji"].strip(),
            "anchor": r.get("anchor") or None,
            "reason": (r.get("reason") or "").strip(),
            "source": "llm",
        })

    cleaned.sort(key=lambda a: a["start"])
    return cleaned[:max_animations]
