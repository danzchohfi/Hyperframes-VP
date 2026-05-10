"""AI-powered music suggestion + pluggable music search providers.

Flow:
  transcript.json → suggest_music() → MusicSuggestion (mood, genre, BPM, ...)
  query           → provider.search() → list[MusicTrack]

The Epidemic Sound provider is configured via env:
  EPIDEMIC_SOUND_BASE_URL   default https://api.epidemicsound.com/v0
  EPIDEMIC_SOUND_TOKEN      partner bearer token (required to enable provider)
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlencode, quote_plus

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field


# --- models -------------------------------------------------------------------

class MusicSuggestion(BaseModel):
    mood: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    bpm_min: int = 90
    bpm_max: int = 120
    energy: str = "medium"  # low | medium | high
    instruments: list[str] = Field(default_factory=list)
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    epidemic_search_url: str = ""


class MusicTrack(BaseModel):
    id: str
    title: str
    artist: str | None = None
    bpm: int | None = None
    duration: float | None = None
    genres: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    preview_url: str | None = None
    download_url: str | None = None
    provider: str = "unknown"


# --- AI suggestion ------------------------------------------------------------

_SYSTEM_PROMPT = """You are a music supervisor for short-form video content.
Given a transcript, suggest background music that fits the tone.
Respond with ONLY a JSON object with these fields:
- mood: array of 2-4 mood adjectives (e.g. ["uplifting", "warm"])
- genres: array of 1-3 musical genres (e.g. ["indie pop", "electronic"])
- bpm_min: integer minimum BPM (50-200)
- bpm_max: integer maximum BPM (50-200)
- energy: one of "low", "medium", "high"
- instruments: array of 1-4 instruments
- description: one-sentence description
- keywords: array of 3-6 search keywords for music libraries

Be concise and specific. Adjust BPM and energy to the speaker's pace and topic."""


async def suggest_music(transcript_text: str, *, duration: float | None = None) -> MusicSuggestion:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = AsyncOpenAI(api_key=api_key)

    snippet = transcript_text.strip()
    if len(snippet) > 4000:
        snippet = snippet[:4000] + "..."

    user_msg = f"Transcript:\n\n{snippet}"
    if duration:
        user_msg += f"\n\nDuration: {duration:.1f} seconds"

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.4,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    data: dict[str, Any] = json.loads(raw)

    suggestion = MusicSuggestion(
        mood=_as_list(data.get("mood")),
        genres=_as_list(data.get("genres")),
        bpm_min=int(data.get("bpm_min") or 90),
        bpm_max=int(data.get("bpm_max") or 120),
        energy=str(data.get("energy") or "medium").lower(),
        instruments=_as_list(data.get("instruments")),
        description=str(data.get("description") or ""),
        keywords=_as_list(data.get("keywords")),
    )
    suggestion.epidemic_search_url = _epidemic_search_url(suggestion)
    return suggestion


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    if isinstance(v, str):
        return [v] if v else []
    return []


def _epidemic_search_url(s: MusicSuggestion) -> str:
    """Build a public Epidemic Sound search URL pre-filled with the suggestion."""
    parts = (s.mood + s.genres + s.keywords)[:5]
    if not parts:
        return "https://www.epidemicsound.com/search/"
    q = " ".join(parts)
    return "https://www.epidemicsound.com/search/?" + urlencode({"term": q})


# --- providers ----------------------------------------------------------------

class MusicProvider(ABC):
    name: str = "unknown"

    @abstractmethod
    async def search(self, suggestion: MusicSuggestion, *, limit: int = 10) -> list[MusicTrack]:
        ...


class EpidemicSoundProvider(MusicProvider):
    """Calls the Epidemic Sound partner API.

    The exact endpoints/parameters depend on your partner contract — adjust
    `_endpoint` / `_params` to match. Defaults aim at v0 partner-content-api
    `/tracks/search` shape.
    """

    name = "epidemic_sound"

    def __init__(self, token: str, base_url: str | None = None):
        self.token = token
        self.base = (base_url or os.environ.get("EPIDEMIC_SOUND_BASE_URL")
                     or "https://api.epidemicsound.com/v0").rstrip("/")

    async def search(self, suggestion: MusicSuggestion, *, limit: int = 10) -> list[MusicTrack]:
        params = self._params(suggestion, limit)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "Hyperframes-VP/0.1",
        }
        url = f"{self.base}/tracks/search"
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
        return [self._normalize(item) for item in (data.get("tracks") or data.get("results") or [])][:limit]

    def _params(self, s: MusicSuggestion, limit: int) -> dict[str, Any]:
        return {
            "q": " ".join(s.keywords or s.mood or s.genres),
            "moods": ",".join(s.mood) if s.mood else None,
            "genres": ",".join(s.genres) if s.genres else None,
            "bpm_min": s.bpm_min,
            "bpm_max": s.bpm_max,
            "limit": limit,
        }

    def _normalize(self, t: dict[str, Any]) -> MusicTrack:
        return MusicTrack(
            id=str(t.get("id") or t.get("trackId") or ""),
            title=str(t.get("title") or t.get("name") or "Untitled"),
            artist=t.get("mainArtist") or t.get("artist") or t.get("artists", [{}])[0].get("name"),
            bpm=int(t["bpm"]) if t.get("bpm") else None,
            duration=float(t["lengthInSeconds"]) if t.get("lengthInSeconds") else None,
            genres=_as_list(t.get("genres")),
            moods=_as_list(t.get("moods")),
            preview_url=t.get("previewUrl") or t.get("preview"),
            download_url=t.get("downloadUrl"),
            provider=self.name,
        )


def get_provider() -> MusicProvider | None:
    """Returns a configured provider if env credentials are present."""
    es_token = os.environ.get("EPIDEMIC_SOUND_TOKEN")
    if es_token:
        return EpidemicSoundProvider(es_token)
    return None
