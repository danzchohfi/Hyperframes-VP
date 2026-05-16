"""Prompt-to-animation planner via Claude Sonnet 4.6 tool use.

Takes a free-form prompt from the user ("estilo retrô anos 80, com flashes
nos pontos altos") plus the project's transcript + chapters + soundbites,
and returns a list of `reels_animations.json` entries that the existing
1-click pipeline already knows how to render.

Why tool use instead of plain JSON output: forced tool_choice with a
strict input_schema is the most reliable way to constrain Claude to a
known shape. The schema mirrors the fields that `normalize_animation`
will accept; anything else gets dropped or defaulted downstream so the
worst case is benign.

Caching: the system prompt + animation-type catalog are large and
shared across every call for every project. We mark them with
cache_control so the second-and-onward request pays ~0.1× input cost
for that prefix. The volatile bits (transcript snippet, user prompt)
go after the cache breakpoint so a different prompt still hits the
cache.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from . import reels_animations as ra

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 4096

# Hard caps so a runaway LLM can't produce a 400-animation composition.
MAX_ANIMATIONS = 24
# How much transcript text to include. Whisper's "text" field can be long;
# we trim to keep request size reasonable and to keep cache hits possible
# (a 90-min podcast has ~15k tokens of transcript — too volatile to cache
# the trailing user message, but the system prompt above DOES cache).
TRANSCRIPT_CHAR_BUDGET = 12_000


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def _animation_tool() -> dict[str, Any]:
    """Tool schema the model is forced to fill. Mirrors the shape that
    reels_animations.normalize_animation will accept.

    Hook and CTA items can carry `custom_html` + `custom_css` for fully
    bespoke visual treatments (the body of the card). The server runs
    both through a strict sanitizer (whitelist tags / attrs, no scripts,
    no remote URLs, CSS auto-scoped to the item's container) before
    persisting, so even a maliciously-prompted plan can't escape the
    composition sandbox.
    """
    types = sorted(ra.ANIMATION_TYPES.keys())
    # Collect every variant across types into one enum — the model will
    # pick one per item; normalize_animation later clamps it to the
    # actually-supported variants for that type.
    all_variants = sorted({v for spec in ra.ANIMATION_TYPES.values() for v in spec.get("variants", [])})
    easing_names = sorted(ra.EASINGS.keys())
    return {
        "name": "emit_animation_plan",
        "description": (
            "Emit the list of animated overlays to add to the podcast video, "
            "tuned to the user's stylistic prompt. Each animation is a "
            "self-contained overlay clip — pick the right type, place it at "
            "a meaningful moment, keep text short (under 6 words for "
            "callouts / hooks). For hook_card and cta_end you can optionally "
            "design a fully custom card via custom_html + custom_css."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {
                    "type": "string",
                    "description": "1-2 sentence summary of the stylistic direction you chose.",
                },
                "animations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_ANIMATIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": types},
                            "start": {"type": "number", "description": "Seconds from the start of the source."},
                            "duration": {"type": "number", "description": "Seconds. 0.4 - 5.0 is the sane range."},
                            "anchor": {
                                "type": "string",
                                "enum": [
                                    "center", "top", "bottom",
                                    "top-left", "top-right",
                                    "bottom-left", "bottom-right",
                                    "center-left", "center-right",
                                ],
                            },
                            "text": {"type": "string", "description": "Main text. Keep short."},
                            "sub": {"type": "string", "description": "Optional subtitle / supporting line."},
                            "emoji": {"type": "string", "description": "Single emoji for emoji_burst / sticker variants."},
                            "variant": {"type": "string", "enum": all_variants or ["default"]},
                            "easing": {
                                "type": "string",
                                "enum": easing_names,
                                "description": (
                                    "Optional named easing preset for the entrance. "
                                    "Pick the curve that matches the requested style "
                                    "(e.g. 'apple-emphasis' / 'apple-decel' for "
                                    "Apple-keynote feel, 'cinema-punch' for MotionVFX-"
                                    "style impact). Leave empty to use the type's default."
                                ),
                            },
                            "show_logo": {"type": "boolean"},
                            "rationale": {"type": "string", "description": "Why this animation here (1 line)."},
                            # Free-form HTML for hook_card / cta_end only. The
                            # server sanitizes (whitelist tags, no scripts,
                            # relative URLs only) and the CSS gets auto-
                            # scoped, so think of this as "everything inside
                            # the card body". Container, entrance/exit motion,
                            # and positioning come from the existing variant
                            # system — you don't need to (and can't) write
                            # those yourself.
                            "custom_html": {
                                "type": "string",
                                "description": (
                                    "Optional. ONLY for hook_card / cta_end. "
                                    "HTML fragment that REPLACES the templated "
                                    "card body. No <script>, no remote URLs, no "
                                    "event handlers. Use SVG freely for decoration. "
                                    "Keep under 3000 chars."
                                ),
                                "maxLength": 4000,
                            },
                            "custom_css": {
                                "type": "string",
                                "description": (
                                    "Optional. Companion CSS for custom_html. "
                                    "Auto-scoped to this card's container, so "
                                    "selectors like '.title' only apply inside "
                                    "this card. No @import, no remote url(). "
                                    "Keep under 1500 chars."
                                ),
                                "maxLength": 1500,
                            },
                        },
                        "required": ["type", "start", "duration", "text"],
                    },
                },
            },
            "required": ["animations"],
        },
    }


SYSTEM_PROMPT = """You are the motion-design director for a podcast-to-reels pipeline.

You receive: the user's stylistic prompt, a transcript excerpt, chapter
markers, and soundbite timestamps. You emit a list of animated overlays
(emit_animation_plan) that the renderer applies on top of the video.

Design rules:
- Pace: typically 4–10 animations for a 30s reel, 8–18 for a 1–3min clip,
  12–24 for anything longer. Less is usually more.
- ONE hook_card at the very start (start ≤ 0.5s, duration ≈ 1.4s).
- ONE cta_end near the actual end of the source (use the last chapter's
  end if available, otherwise omit if you don't know the duration).
- Use text_callout / word_zoom / number_pop on soundbites and chapter
  transitions, NOT on filler moments.
- Keep text under 6 words for callouts and hooks. Punchy beats wordy.
- emoji_burst is a salt-not-spice: max 2 in any reel, only on real
  emotional/comedic peaks.
- lower_third only when the transcript clearly introduces a person by
  name+role; never invent a name.
- The user's stylistic prompt is the boss: if they say "minimal", lean
  on word_zoom + text_callout pill. If they say "energetic", more
  emoji_burst + number_pop bold. If they specify colors, USE them — see
  custom_html below.
- Times must be inside the source duration. If you don't know the
  duration, stay before the last soundbite or chapter end you saw.

Audio-reactive timing:
- When a `<beats>` block is present in the context, the BPM and a list
  of strong-beat timestamps come from the actual audio (drum / onset
  detection). Align animation `start` values to beats whenever possible:
  hook entrances on the first beat after speech starts, callouts /
  emphasis on accent beats, the CTA on the last beat before the outro.
- The server post-snaps starts to the nearest beat within ±0.15s, so
  you don't need to hit the exact decimal — get within that window.
- Density should track BPM: high BPM (≥130) tolerates more, denser
  animations; low BPM (≤80) wants breathing room between events.

Motion language (named easings + premium variants):
- For each animation you can set `easing` to one of:
    apple-emphasis  — soft snap, Apple keynote entrance.
    apple-decel     — slow-out, Pages/Numbers reveal feel.
    cinema-punch    — overshoot, MotionVFX impact.
    swift-release   — fast in, slow settle.
    glide-in        — smooth hero text reveal.
  Pick the curve that matches the user's stylistic prompt. If they say
  "Apple keynote", lean on apple-emphasis + apple-decel. If they say
  "cinematic / MotionVFX / energetic", lean on cinema-punch + swift-release.
  Omit `easing` to use a sensible per-type default.
- The hook_card and cta_end types now support a `keynote` variant on
  top of bold / gradient / minimal. The `keynote` variant uses Apple-
  style cinematic typography: per-character entrance with blur and
  staggered reveal, layered drop-shadows, depth-aware perspective.
  Use it when the user asks for "Apple keynote", "premium", "elegant",
  "minimal cinematic". Pair with apple-emphasis or apple-decel easing.

Custom hook / CTA design (advanced):
- For hook_card and cta_end items ONLY, you can emit custom_html +
  custom_css to design the card body from scratch. This is the right
  call when the user asks for a distinctive look ("retro 80s neon",
  "ransom-note", "newspaper headline", "VHS glitch", etc).
- DO NOT include: <script>, <iframe>, <link>, <style>, on* attributes,
  remote URLs (http://, https://), @import, @font-face. The sanitizer
  will strip them silently.
- DO use: inline SVG for decoration (gradients, blur filters, shapes),
  gradient/glow effects via CSS, custom typography via font-family
  (system fonts only — no @import / Google Fonts), CSS keyframes for
  internal micro-motion (the OUTER entrance/exit is handled for you).
- Selectors in custom_css are auto-prefixed with the item's container
  ID, so a rule like `.title { color: red }` only affects this card.
- Keep custom_html <= 3000 chars and custom_css <= 1500 chars. Less is
  more — three well-chosen elements beat a maximalist nightmare.
- The standard `text` / `sub` fields are still required (used as
  fallback if sanitization strips everything, and for screen readers /
  exports).

Output is structured via the tool — fill it directly, no prose."""


def _build_context(
    *,
    transcript: dict[str, Any] | None,
    chapters: list[dict[str, Any]] | None,
    soundbites: list[dict[str, Any]] | None,
    duration: float | None,
    beats: dict[str, Any] | None = None,
) -> str:
    """Compact context block. Goes AFTER the cache breakpoint, so it
    can vary per call without invalidating the system-prompt cache."""
    parts: list[str] = []
    if duration:
        parts.append(f"<source_duration>{duration:.1f}s</source_duration>")
    if beats:
        from . import audio_beats as ab
        block = ab.beats_context_block(beats, chapters=chapters, soundbites=soundbites)
        if block:
            parts.append(block)
    if chapters:
        rows = "\n".join(
            f"- {float(c.get('start') or 0):.1f}s → {float(c.get('end') or 0):.1f}s · {c.get('name') or c.get('title') or ''}"
            for c in chapters[:30]
        )
        parts.append(f"<chapters>\n{rows}\n</chapters>")
    if soundbites:
        rows = "\n".join(
            f"- {float(b.get('start') or 0):.1f}s → {float(b.get('end') or 0):.1f}s · {(b.get('text') or '')[:140]}"
            for b in soundbites[:30]
        )
        parts.append(f"<soundbites>\n{rows}\n</soundbites>")
    if transcript:
        text = (transcript.get("text") or "").strip()
        if len(text) > TRANSCRIPT_CHAR_BUDGET:
            # Take a head + tail slice so the model sees opener and closer.
            head = text[: TRANSCRIPT_CHAR_BUDGET // 2]
            tail = text[-TRANSCRIPT_CHAR_BUDGET // 2:]
            text = f"{head}\n[...trimmed...]\n{tail}"
        parts.append(f"<transcript>\n{text}\n</transcript>")
    return "\n\n".join(parts)


def plan_from_prompt(
    *,
    user_prompt: str,
    transcript: dict[str, Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
    soundbites: list[dict[str, Any]] | None = None,
    duration: float | None = None,
    beats: dict[str, Any] | None = None,
    snap_to_beats: bool = True,
) -> dict[str, Any]:
    """Call Claude with forced tool use, return the parsed animation plan.

    Raises RuntimeError with a human-readable message on auth / shape
    failures so the API layer can surface a 4xx/5xx properly.
    """
    if not user_prompt or not user_prompt.strip():
        raise ValueError("user_prompt is required")

    client = _client()
    tool = _animation_tool()
    context = _build_context(
        transcript=transcript, chapters=chapters,
        soundbites=soundbites, duration=duration, beats=beats,
    )
    user_content = (
        f"<style_prompt>\n{user_prompt.strip()}\n</style_prompt>\n\n{context}"
        if context else f"<style_prompt>\n{user_prompt.strip()}\n</style_prompt>"
    )

    # System prompt + tool definitions are stable across calls. Mark the
    # last system block with cache_control so the prefix (tools + system)
    # caches; subsequent calls pay ~0.1× input price on the cached part.
    # NB: tools render at position 0 and are included in the cached prefix
    # for free — no marker needed on them.
    system_blocks = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_blocks,
            tools=[tool],
            # Force the model to emit the tool; never an open-ended text
            # response. disable_parallel_tool_use ensures exactly one call.
            tool_choice={
                "type": "tool",
                "name": tool["name"],
                "disable_parallel_tool_use": True,
            },
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.AuthenticationError:
        raise RuntimeError("ANTHROPIC_API_KEY inválida ou ausente")
    except anthropic.RateLimitError as e:
        raise RuntimeError(f"Anthropic rate limit; tente em alguns segundos: {e}")
    except anthropic.BadRequestError as e:
        raise RuntimeError(f"Prompt rejeitado pelo Claude: {e}")
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude API erro {getattr(e, 'status_code', '?')}: {e}")

    # Find the tool_use block (we forced it, but the SDK still returns a
    # list of content blocks). Defensive: log usage for cache observability.
    usage = getattr(resp, "usage", None)
    if usage:
        log.info(
            "anim_planner: in=%s out=%s cache_read=%s cache_write=%s",
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
            getattr(usage, "cache_read_input_tokens", 0),
            getattr(usage, "cache_creation_input_tokens", 0),
        )

    tool_block = next(
        (b for b in resp.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_block is None:
        raise RuntimeError("Claude não emitiu o plano (sem tool_use no retorno)")

    raw = tool_block.input or {}
    raw_anims = raw.get("animations") or []
    if not raw_anims:
        raise RuntimeError("Claude devolveu um plano vazio")

    # Run each item through normalize_animation. It clamps to valid
    # variants, defaults durations, drops bogus fields, etc. Anything
    # that fails outright we skip rather than fail the whole batch.
    normalized: list[dict[str, Any]] = []
    for a in raw_anims:
        try:
            normalized.append(ra.normalize_animation(a, source_duration=duration))
        except Exception as e:
            log.warning("anim_planner: dropped bad item %r: %s", a, e)
    if not normalized:
        raise RuntimeError("Todos os itens do plano foram rejeitados pela validação")

    # Post-pass: snap animation starts to the nearest beat / onset.
    # The LLM is told about the beats but doesn't always nail the
    # alignment; the snap window (±0.15s) is tight enough that we
    # don't drift placements far from the model's intent.
    snapped_count = 0
    if snap_to_beats and beats and (beats.get("beats") or beats.get("onsets")):
        from . import audio_beats as ab
        snapped = ab.snap_animations_to_beats(normalized, beats)
        snapped_count = sum(1 for a in snapped if "snapped_from" in a)
        normalized = snapped

    return {
        "rationale": raw.get("rationale") or "",
        "animations": normalized,
        "model": MODEL,
        "beats_snapped": snapped_count,
    }
