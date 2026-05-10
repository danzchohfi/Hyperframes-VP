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


class SpeakerStyle(BaseModel):
    name: str = "Speaker"
    color: str = "#ffffff"
    chip_bg: str | None = None  # optional pill background; defaults to palette.background
    role: str | None = None     # for lower-thirds ("Host", "CEO da Acme", ...)


class LogoSettings(BaseModel):
    enabled: bool = False
    position: str = "top-right"  # top-left | top-right | bottom-left | bottom-right
    size: float = 0.10           # 10% of the frame width


class CTASettings(BaseModel):
    text: str = ""
    sub: str | None = None
    start: float = 0.0
    duration: float = 4.0
    position: str = "bottom"     # top | bottom | center


class BrandBook(BaseModel):
    name: str = "Brand"
    tagline: str | None = None
    logo_url: str | None = None
    palette: BrandPalette = Field(default_factory=BrandPalette)
    typography: BrandTypography = Field(default_factory=BrandTypography)
    caption_position: str = "bottom"  # bottom | center
    caption_color: str | None = None  # defaults to palette.foreground
    caption_highlight: str | None = None  # defaults to palette.accent
    caption_style: str = "minimal"    # minimal | tiktok | podcast
    speakers: dict[str, SpeakerStyle] = Field(default_factory=dict)  # {"A": {...}, "B": {...}}
    intro_title: str | None = None
    intro_subtitle: str | None = None
    outro_text: str | None = None
    logo: LogoSettings = Field(default_factory=LogoSettings)
    lower_thirds: bool = False        # show speaker name + role chip during talk
    ctas: list[CTASettings] = Field(default_factory=list)
