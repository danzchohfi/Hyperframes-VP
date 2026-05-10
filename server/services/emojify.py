"""Auto-decorate caption text with relevant emojis (TikTok-style).

Two paths:
  1. Local heuristic: a small dictionary of keyword → emoji mappings.
  2. LLM: ask the model to pick 1 emoji per content line. Optional, gated by
     OPENAI_API_KEY.

The local path is deterministic and no-network; the LLM path is more nuanced.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import AsyncOpenAI

# Conservative dictionary covering common pt-BR + en topics
KEYWORD_EMOJI: dict[str, str] = {
    # work / business
    "venda": "💰", "sales": "💰", "money": "💰", "dinheiro": "💰",
    "negócio": "💼", "business": "💼",
    "marketing": "📢",
    "produto": "📦", "product": "📦",
    "cliente": "🤝", "client": "🤝", "customer": "🤝",
    "trabalho": "💼", "work": "💼", "job": "💼",
    "time": "⏰", "tempo": "⏰",
    # tech
    "ai": "🤖", "ia": "🤖", "tecnologia": "💻", "tech": "💻",
    "código": "💻", "code": "💻", "software": "💾",
    "vídeo": "🎬", "video": "🎬",
    "edição": "✂", "edit": "✂",
    "câmera": "📷", "camera": "📷",
    # life / emotion
    "amor": "❤", "love": "❤",
    "feliz": "😊", "happy": "😊",
    "rir": "😂", "laugh": "😂",
    "trágico": "😢", "sad": "😢",
    "wow": "🤯", "incrível": "🤯", "amazing": "🤯",
    "fogo": "🔥", "fire": "🔥",
    "boom": "💥",
    # travel / lifestyle
    "viagem": "✈", "travel": "✈",
    "casa": "🏠", "home": "🏠",
    "comida": "🍔", "food": "🍔",
    "café": "☕", "coffee": "☕",
    # social / communication
    "ouvir": "👂", "listen": "👂",
    "falar": "🗣", "talk": "🗣", "speak": "🗣",
    "ver": "👀", "see": "👀", "watch": "👀",
    "ler": "📖", "read": "📖", "book": "📖", "livro": "📖",
    "ideia": "💡", "idea": "💡",
    "pergunta": "❓", "question": "❓",
    "resposta": "💬", "answer": "💬",
    # progress
    "novo": "✨", "new": "✨",
    "começar": "🚀", "start": "🚀", "launch": "🚀",
    "vencer": "🏆", "win": "🏆",
    "perder": "😬", "lose": "😬",
}

_PUNCT = re.compile(r"[.,!?;:'\"()\-—–…]+")


def decorate_local(text: str, *, max_per_line: int = 1) -> str:
    """Append up to max_per_line emojis to a line based on keyword matches."""
    seen: list[str] = []
    for w in (_PUNCT.sub("", text).lower().split()):
        em = KEYWORD_EMOJI.get(w)
        if em and em not in seen:
            seen.append(em)
            if len(seen) >= max_per_line:
                break
    if not seen:
        return text
    return text.rstrip() + " " + "".join(seen)


async def decorate_llm(lines: list[str]) -> list[str]:
    """Ask the model to pick one emoji per line. Returns lines with emoji
    appended. Falls back to local heuristic on failure."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return [decorate_local(t) for t in lines]
    if not lines:
        return []

    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
    prompt = (
        "For each numbered line, return at most one fitting emoji that matches "
        "the dominant concept (or 'none' if nothing fits). Reply with ONLY a JSON "
        'object: {"emojis":["🔥", "none", "💡", ...]} matching the input order.\n\n'
        f"Lines:\n{numbered}"
    )
    try:
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You decorate short caption lines with one tasteful emoji each. Avoid overuse."},
                {"role": "user", "content": prompt},
            ],
        )
        data: dict[str, Any] = json.loads(resp.choices[0].message.content or "{}")
        emojis = data.get("emojis") or []
        out: list[str] = []
        for i, line in enumerate(lines):
            em = (emojis[i] if i < len(emojis) else "") or ""
            if em.lower().strip() in ("", "none"):
                out.append(line)
            else:
                out.append(line.rstrip() + " " + em.strip())
        return out
    except Exception:
        return [decorate_local(t) for t in lines]
