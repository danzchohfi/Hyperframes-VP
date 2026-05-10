"""Hyperframes-style preset templates. Each template is a BrandBook seed +
default render options. Applying a template overwrites brand.json.
"""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "talking_head_vertical": {
        "label": "Talking head vertical (Reels)",
        "aspect": "9:16",
        "render_source": "graded",
        "include_chapter_cards": False,
        "brand": {
            "name": "Talking Head",
            "tagline": None,
            "intro_title": None,
            "intro_subtitle": None,
            "outro_text": None,
            "caption_position": "bottom",
            "caption_style": "tiktok",
            "palette": {
                "primary": "#a78bfa",
                "secondary": "#3b82f6",
                "accent": "#facc15",
                "background": "#06060a",
                "foreground": "#ffffff",
            },
            "typography": {
                "title_family": "Inter", "title_weight": "700",
                "body_family": "Inter", "body_weight": "400",
            },
        },
    },
    "podcast_horizontal": {
        "label": "Podcast horizontal (YouTube)",
        "aspect": "16:9",
        "render_source": "graded",
        "include_chapter_cards": True,
        "brand": {
            "name": "Podcast",
            "tagline": "Conversations that matter",
            "intro_title": "Podcast",
            "intro_subtitle": "Episode {n}",
            "outro_text": "Subscribe for more",
            "caption_position": "bottom",
            "caption_style": "podcast",
            "palette": {
                "primary": "#f59e0b",
                "secondary": "#06b6d4",
                "accent": "#ec4899",
                "background": "#0f1117",
                "foreground": "#f8fafc",
            },
            "typography": {
                "title_family": "Inter", "title_weight": "600",
                "body_family": "Inter", "body_weight": "300",
            },
            "speakers": {
                "A": {"name": "Host", "color": "#f59e0b"},
                "B": {"name": "Convidado", "color": "#06b6d4"},
            },
        },
    },
    "podcast_clip_vertical": {
        "label": "Podcast clip 9:16 (Reels)",
        "aspect": "9:16",
        "render_source": "roughcut",
        "include_chapter_cards": False,
        "brand": {
            "name": "Podcast",
            "caption_position": "bottom",
            "caption_style": "tiktok",
            "palette": {
                "primary": "#facc15",
                "secondary": "#a78bfa",
                "accent": "#ef4444",
                "background": "#08080f",
                "foreground": "#ffffff",
            },
            "typography": {
                "title_family": "Inter", "title_weight": "800",
                "body_family": "Inter", "body_weight": "500",
            },
            "speakers": {
                "A": {"name": "A", "color": "#facc15"},
                "B": {"name": "B", "color": "#a78bfa"},
            },
        },
    },
    "tutorial_clean": {
        "label": "Tutorial clean (vertical)",
        "aspect": "9:16",
        "render_source": "roughcut",
        "include_chapter_cards": True,
        "brand": {
            "name": "Tutorial",
            "tagline": None,
            "intro_title": None,
            "intro_subtitle": None,
            "outro_text": None,
            "caption_position": "bottom",
            "caption_style": "minimal",
            "palette": {
                "primary": "#34d399",
                "secondary": "#0ea5e9",
                "accent": "#f43f5e",
                "background": "#0a0c11",
                "foreground": "#ffffff",
            },
            "typography": {
                "title_family": "Inter", "title_weight": "600",
                "body_family": "Inter", "body_weight": "300",
            },
        },
    },
    "testimonial_square": {
        "label": "Testimonial square (Feed)",
        "aspect": "1:1",
        "render_source": "graded",
        "include_chapter_cards": False,
        "brand": {
            "name": "Testimonial",
            "tagline": "Real stories",
            "intro_title": None,
            "intro_subtitle": None,
            "outro_text": None,
            "caption_position": "center",
            "caption_style": "minimal",
            "palette": {
                "primary": "#8b5cf6",
                "secondary": "#3b82f6",
                "accent": "#fbbf24",
                "background": "#0e0e15",
                "foreground": "#ffffff",
            },
            "typography": {
                "title_family": "Inter", "title_weight": "600",
                "body_family": "Inter", "body_weight": "400",
            },
        },
    },
}


def list_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": tid,
            "label": t["label"],
            "aspect": t["aspect"],
            "render_source": t["render_source"],
            "include_chapter_cards": t["include_chapter_cards"],
            "brand": t["brand"],
        }
        for tid, t in TEMPLATES.items()
    ]


def get(tid: str) -> dict[str, Any]:
    if tid not in TEMPLATES:
        raise KeyError(tid)
    return TEMPLATES[tid]
