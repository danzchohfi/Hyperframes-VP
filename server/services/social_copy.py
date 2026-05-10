"""Generate post copy (caption, hashtags, hook, thumbnail title) for a video.

Driven by GPT-4o-mini, using the title/logline + transcript text + brand name.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class SocialCopy(BaseModel):
    hook: str = ""
    caption: str = ""
    long_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    thumbnail_title: str = ""
    youtube_title: str = ""
    youtube_description: str = ""


_SYSTEM = """You write social-media copy for short-form video.

Inputs: brand name, story title + logline (if any), transcript snippet, and
target language. Reply with ONLY a JSON object:

{
  "hook": "<6-10 words; first frame text or scroll-stopper>",
  "caption": "<one short sentence for Instagram Reels / TikTok>",
  "long_caption": "<2-3 short sentences with line breaks for Instagram>",
  "hashtags": ["#tag1", "#tag2", ...]   // 6-10 relevant hashtags
  "thumbnail_title": "<3-5 words, pun-y, all-caps acceptable>",
  "youtube_title": "<60 chars max>",
  "youtube_description": "<2-3 sentences for YouTube description>"
}

Match the brand voice if visible in the transcript. Use the target language."""


async def generate(
    *,
    transcript_text: str,
    title: str | None = None,
    logline: str | None = None,
    brand_name: str | None = None,
    language: str = "pt",
) -> SocialCopy:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    snippet = (transcript_text or "").strip()
    if len(snippet) > 2000:
        snippet = snippet[:2000] + "..."

    user = (
        f"Language: {language}\n"
        f"Brand: {brand_name or 'unknown'}\n"
        f"Title: {title or 'untitled'}\n"
        f"Logline: {logline or 'n/a'}\n\n"
        f"Transcript:\n{snippet}\n"
    )
    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.7,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    data: dict[str, Any] = json.loads(resp.choices[0].message.content or "{}")
    hashtags = data.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [h for h in hashtags.split() if h]
    hashtags = [str(h).lstrip("#").strip() for h in hashtags if h]
    hashtags = [f"#{h}" for h in hashtags if h][:12]
    return SocialCopy(
        hook=str(data.get("hook") or "").strip(),
        caption=str(data.get("caption") or "").strip(),
        long_caption=str(data.get("long_caption") or "").strip(),
        hashtags=hashtags,
        thumbnail_title=str(data.get("thumbnail_title") or "").strip(),
        youtube_title=str(data.get("youtube_title") or "").strip(),
        youtube_description=str(data.get("youtube_description") or "").strip(),
    )
