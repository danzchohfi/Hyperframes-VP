"""Brand-book model used to drive Hyperframes intro/outro + caption styling."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BrandPalette(BaseModel):
    primary: str = "#a78bfa"
    secondary: str = "#3b82f6"
    background: str = "#06060a"
    foreground: str = "#ffffff"
    accent: str = "#f472b6"


class BrandTypography(BaseModel):
    title_family: str = "Inter"
    title_weight: str = "600"
    body_family: str = "Inter"
    body_weight: str = "300"


class BrandBook(BaseModel):
    name: str = "Brand"
    tagline: str | None = None
    logo_url: str | None = None
    palette: BrandPalette = Field(default_factory=BrandPalette)
    typography: BrandTypography = Field(default_factory=BrandTypography)
    caption_position: str = "bottom"  # bottom | center
    caption_color: str | None = None  # defaults to palette.foreground
    caption_highlight: str | None = None  # defaults to palette.accent
    intro_title: str | None = None
    intro_subtitle: str | None = None
    outro_text: str | None = None
