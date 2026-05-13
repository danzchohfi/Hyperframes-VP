"""Rasterize a reel animation to a single peak-state PNG (PIL).

Used by the quick-preview path: instead of running Hyperframes + GSAP,
we burn each animation into a PNG at its visual climax and ask ffmpeg
to overlay it with a small fade in/out at the right time. The result
is a lo-fi but accurate MP4 preview in ~10 seconds.

The CSS source of truth lives in `reels_animations.shared_css`. Each
branch below mirrors the corresponding CSS values (paddings, font
ratios, gradient stops) so the PNG looks like a frozen frame of the
live HTML preview.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .brand import BrandBook

log = logging.getLogger(__name__)


# --- font discovery ---------------------------------------------------------

# Render order: explicit env var → fonts-dejavu (Dockerfile) → macOS
# system stack → bundled fallback. We never throw — if everything fails,
# PIL's default bitmap font shows text (ugly but functional).
_FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/SF-Pro-Display-Bold.otf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
_FONT_CANDIDATES_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/SF-Pro-Display-Regular.otf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
]


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REG):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    # Falling back to default — text will still render but quality is poor.
    return ImageFont.load_default()  # type: ignore[return-value]


# --- color helpers ----------------------------------------------------------

def _hex(c: str, default: str = "#ffffff") -> tuple[int, int, int, int]:
    c = (c or "").strip()
    if not c:
        c = default
    if c.startswith("#"):
        c = c[1:]
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) == 6:
        try:
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255)
        except ValueError:
            pass
    if len(c) == 8:
        try:
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), int(c[6:8], 16))
        except ValueError:
            pass
    return (255, 255, 255, 255)


def _with_alpha(rgb: tuple[int, int, int, int], a: int) -> tuple[int, int, int, int]:
    return (rgb[0], rgb[1], rgb[2], max(0, min(255, a)))


def _gradient_diagonal(
    size: tuple[int, int],
    color_a: tuple[int, int, int, int],
    color_b: tuple[int, int, int, int],
    color_c: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    """Cheap diagonal gradient by drawing horizontal lines + a CSS-ish
    135deg matrix tilt. Good enough for preview."""
    w, h = size
    img = Image.new("RGBA", (w, h), color_a)
    draw = ImageDraw.Draw(img)
    total = max(w + h, 1)
    for d in range(total):
        if color_c is not None:
            if d < total / 2:
                f = d / (total / 2)
                r = int(color_a[0] + (color_b[0] - color_a[0]) * f)
                g = int(color_a[1] + (color_b[1] - color_a[1]) * f)
                b = int(color_a[2] + (color_b[2] - color_a[2]) * f)
            else:
                f = (d - total / 2) / (total / 2)
                r = int(color_b[0] + (color_c[0] - color_b[0]) * f)
                g = int(color_b[1] + (color_c[1] - color_b[1]) * f)
                b = int(color_b[2] + (color_c[2] - color_b[2]) * f)
        else:
            f = d / total
            r = int(color_a[0] + (color_b[0] - color_a[0]) * f)
            g = int(color_a[1] + (color_b[1] - color_a[1]) * f)
            b = int(color_a[2] + (color_b[2] - color_a[2]) * f)
        draw.line([(d, 0), (0, d)], fill=(r, g, b, 255))
    return img


def _rounded_rect(
    img: Image.Image, box: tuple[int, int, int, int],
    radius: int, fill: tuple[int, int, int, int],
    border_color: tuple[int, int, int, int] | None = None,
    border_width: int = 0,
) -> None:
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=border_color, width=border_width)


def _measure(text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    bbox = font.getbbox(text)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def _text_with_shadow(
    img: Image.Image, xy: tuple[int, int], text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int, int],
    shadow: tuple[int, int, int, int] = (0, 0, 0, 180),
    blur: int = 6,
) -> None:
    if not text:
        return
    if blur > 0:
        shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow_layer).text((xy[0] + 2, xy[1] + 3), text, font=font, fill=shadow)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(shadow_layer)
    ImageDraw.Draw(img).text(xy, text, font=font, fill=color)


# --- logo loading -----------------------------------------------------------

def _load_logo(project_dir: Path, target_h: int) -> Image.Image | None:
    for name in ("logo.png", "logo.jpg", "logo.jpeg", "logo.webp"):
        p = project_dir / name
        if p.exists():
            try:
                img = Image.open(p).convert("RGBA")
                ratio = target_h / max(img.height, 1)
                w = max(1, int(img.width * ratio))
                return img.resize((w, target_h), Image.LANCZOS)
            except Exception:
                continue
    if (project_dir / "logo.svg").exists():
        log.warning("preview rasterizer: SVG logo skipped (PIL doesn't read SVG)")
    return None


# --- main entry point -------------------------------------------------------

def hash_anim(anim: dict, brand_palette: dict | None = None) -> str:
    """Cheap content-hash so we can skip re-rasterizing unchanged animations."""
    keys = ("type", "variant", "text", "sub", "emoji", "show_logo", "anchor")
    payload = {k: anim.get(k) for k in keys}
    payload["palette"] = brand_palette or {}
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]


def rasterize_animation(
    anim: dict[str, Any],
    brand: BrandBook,
    frame_width: int,
    frame_height: int,
    *,
    project_dir: Path | None = None,
) -> Image.Image:
    """Return an RGBA image sized to (frame_width, frame_height) with the
    animation drawn at its anchor position. Pixels outside the animation's
    visual area stay fully transparent — ffmpeg overlays this on top of
    the video so transparency must be exact."""
    img = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
    t = anim.get("type") or "text_callout"
    p = brand.palette
    primary = _hex(p.primary)
    secondary = _hex(p.secondary)
    accent = _hex(p.accent)
    background = _hex(p.background)
    foreground = _hex(p.foreground)

    # Font sizes mirror CSS: width-relative, same ratios as shared_css.
    big = max(24, int(frame_width * 0.085))
    med = max(20, int(frame_width * 0.05))
    sml = max(14, int(frame_width * 0.024))

    text = (anim.get("text") or "").strip()
    sub = (anim.get("sub") or "").strip()
    emoji = (anim.get("emoji") or "").strip()
    variant = anim.get("variant") or "bold"
    show_logo = bool(anim.get("show_logo")) and t in ("hook_card", "cta_end")
    logo = _load_logo(project_dir, target_h=int(frame_width * 0.10)) if (show_logo and project_dir) else None

    if t in ("hook_card", "cta_end"):
        _draw_card(
            img, variant=variant, text=text, sub=sub, logo=logo,
            frame=(frame_width, frame_height),
            primary=primary, secondary=secondary, accent=accent,
            background=background, foreground=foreground,
            font_title=_load_font(med, bold=True),
            font_title_big=_load_font(int(med * 1.3), bold=True),
            font_sub=_load_font(sml, bold=False),
        )

    elif t == "text_callout":
        _draw_callout(
            img, variant=variant, text=text, sub=sub, emoji=emoji,
            anchor=anim.get("anchor", "bottom-left"),
            frame=(frame_width, frame_height),
            primary=primary, accent=accent,
            foreground=foreground, background=background,
            font=_load_font(sml + 2, bold=True),
            font_sub=_load_font(sml, bold=False),
        )

    elif t in ("word_zoom", "number_pop"):
        _draw_big_word(
            img, text=text or "—", sub=sub,
            frame=(frame_width, frame_height),
            accent=accent, background=background, foreground=foreground,
            font_big=_load_font(big, bold=True),
            font_sub=_load_font(sml, bold=False),
        )

    elif t == "lower_third":
        _draw_lower_third(
            img, name=text, role=sub,
            frame=(frame_width, frame_height),
            primary=primary, secondary=secondary, accent=accent,
            foreground=foreground,
            font_name=_load_font(sml + 2, bold=True),
            font_role=_load_font(sml - 2, bold=False),
        )

    elif t == "emoji_burst":
        _draw_emoji(
            img, emoji=emoji or "✨",
            frame=(frame_width, frame_height),
            font=_load_font(big + 8, bold=False),
        )

    elif t == "arrow_highlight":
        _draw_arrow_highlight(
            img, text=text,
            anchor=anim.get("anchor", "bottom"),
            frame=(frame_width, frame_height),
            primary=primary, accent=accent, background=background, foreground=foreground,
            font=_load_font(sml, bold=True),
        )

    else:
        _draw_callout(
            img, variant="pill", text=text or "?", sub="", emoji=emoji,
            anchor=anim.get("anchor", "center"),
            frame=(frame_width, frame_height),
            primary=primary, accent=accent,
            foreground=foreground, background=background,
            font=_load_font(sml + 2, bold=True),
            font_sub=_load_font(sml, bold=False),
        )

    return img


# --- per-type drawers -------------------------------------------------------

def _draw_card(
    img: Image.Image, *,
    variant: str, text: str, sub: str, logo: Image.Image | None,
    frame: tuple[int, int],
    primary, secondary, accent, background, foreground,
    font_title, font_title_big, font_sub,
) -> None:
    w, h = frame
    if variant == "gradient":
        # Full-bleed gradient — primary → accent → secondary
        grad = _gradient_diagonal((w, h), primary, accent, secondary)
        img.alpha_composite(grad)
        # Dark vignette at the bottom for legibility
        vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vignette)
        for y in range(int(h * 0.55), h):
            f = (y - int(h * 0.55)) / max(1, h - int(h * 0.55))
            vd.line([(0, y), (w, y)], fill=(0, 0, 0, int(120 * f)))
        img.alpha_composite(vignette)
        font = font_title_big
        title = text.upper() if text else ""
    elif variant == "minimal":
        font = font_title_big
        title = text
    else:  # bold
        font = font_title
        title = text

    # Compose content (title + optional sub + optional logo) centered.
    tw, th = _measure(title, font) if title else (0, 0)
    sw, sh = _measure(sub, font_sub) if sub else (0, 0)
    logo_h = logo.height if logo else 0
    gap_logo = 16 if logo else 0
    gap_sub = 6 if sub else 0
    total_h = logo_h + gap_logo + th + gap_sub + sh

    cx, cy = w // 2, h // 2
    y = cy - total_h // 2

    if variant == "bold":
        # Translucent card behind content
        pad_x, pad_y = 36, 28
        card_w = max(tw, sw, (logo.width if logo else 0)) + pad_x * 2
        card_h = total_h + pad_y * 2
        x0 = cx - card_w // 2
        y0 = y - pad_y
        _rounded_rect(
            img, (x0, y0, x0 + card_w, y0 + card_h),
            radius=18,
            fill=_with_alpha(background, 220),
            border_color=_with_alpha(primary, 90),
            border_width=2,
        )

    if logo:
        img.alpha_composite(logo, dest=(cx - logo.width // 2, y))
        y += logo_h + gap_logo
    if title:
        if variant == "minimal":
            _text_with_shadow(img, (cx - tw // 2, y), title, font, foreground,
                              shadow=(0, 0, 0, 220), blur=14)
        else:
            ImageDraw.Draw(img).text((cx - tw // 2, y), title, font=font, fill=foreground)
        y += th + gap_sub
    if sub:
        ImageDraw.Draw(img).text(
            (cx - sw // 2, y), sub, font=font_sub,
            fill=_with_alpha(foreground, 200),
        )


def _draw_callout(
    img: Image.Image, *,
    variant: str, text: str, sub: str, emoji: str, anchor: str,
    frame: tuple[int, int],
    primary, accent, foreground, background,
    font, font_sub,
) -> None:
    label = (f"{emoji} {text}" if emoji else text).strip() or "—"
    tw, th = _measure(label, font)
    sw, sh = _measure(sub, font_sub) if sub else (0, 0)

    if variant == "block":
        pad_x, pad_y = 18, 12
        bw = max(tw, sw) + pad_x * 2
        bh = th + pad_y * 2 + (sh + 4 if sub else 0)
        x, y = _anchor_xy(frame, (bw, bh), anchor)
        _rounded_rect(
            img, (x, y, x + bw, y + bh),
            radius=10,
            fill=_with_alpha(background, 235),
            border_color=_with_alpha(accent, 110),
            border_width=1,
        )
        # Left accent stripe
        ImageDraw.Draw(img).rectangle((x, y, x + 4, y + bh), fill=accent)
        ImageDraw.Draw(img).text((x + pad_x, y + pad_y), label, font=font, fill=foreground)
        if sub:
            ImageDraw.Draw(img).text(
                (x + pad_x, y + pad_y + th + 4), sub, font=font_sub,
                fill=_with_alpha(foreground, 180),
            )
    else:  # pill
        pad_x, pad_y = 18, 10
        bw = tw + pad_x * 2
        bh = th + pad_y * 2
        x, y = _anchor_xy(frame, (bw, bh), anchor)
        pill_grad = _gradient_diagonal((bw, bh), primary, accent)
        mask = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw, bh), radius=bh // 2, fill=255)
        img.paste(pill_grad, (x, y), mask)
        ImageDraw.Draw(img).text((x + pad_x, y + pad_y), label, font=font, fill=foreground)


def _draw_big_word(
    img: Image.Image, *,
    text: str, sub: str,
    frame: tuple[int, int],
    accent, background, foreground,
    font_big, font_sub,
) -> None:
    w, h = frame
    tw, th = _measure(text, font_big)
    cx, cy = w // 2, h // 2
    x = cx - tw // 2
    y = cy - th // 2
    _text_with_shadow(img, (x, y), text, font_big, accent,
                      shadow=_with_alpha(accent, 100), blur=20)
    # Hard text on top for crispness
    ImageDraw.Draw(img).text((x, y), text, font=font_big, fill=accent)
    if sub:
        sw, sh = _measure(sub, font_sub)
        ImageDraw.Draw(img).text(
            (cx - sw // 2, y + th + 8), sub, font=font_sub,
            fill=_with_alpha(foreground, 200),
        )


def _draw_lower_third(
    img: Image.Image, *,
    name: str, role: str,
    frame: tuple[int, int],
    primary, secondary, accent, foreground,
    font_name, font_role,
) -> None:
    label = name or "Speaker"
    role_s = role or ""
    tw, th = _measure(label, font_name)
    rw, rh = _measure(role_s, font_role) if role_s else (0, 0)
    pad_x, pad_y = 18, 10
    bw = max(tw, rw) + pad_x * 2
    bh = th + (rh + 4 if role_s else 0) + pad_y * 2
    x, y = _anchor_xy(frame, (bw, bh), "bottom-left")

    grad = _gradient_diagonal((bw, bh), _with_alpha(primary, 220), _with_alpha(secondary, 130))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw, bh), radius=12, fill=255)
    img.paste(grad, (x, y), mask)
    # Left accent stripe
    ImageDraw.Draw(img).rectangle((x, y, x + 4, y + bh), fill=accent)
    ImageDraw.Draw(img).text((x + pad_x, y + pad_y), label, font=font_name, fill=foreground)
    if role_s:
        ImageDraw.Draw(img).text(
            (x + pad_x, y + pad_y + th + 4), role_s, font=font_role,
            fill=_with_alpha(foreground, 180),
        )


def _draw_emoji(
    img: Image.Image, *,
    emoji: str,
    frame: tuple[int, int],
    font,
) -> None:
    # PIL renders emoji as monochrome unless a color-emoji font is installed.
    # On systems with Noto Color Emoji or Apple Color Emoji it'll look right;
    # otherwise it's still readable.
    w, h = frame
    tw, th = _measure(emoji, font)
    x, y = _anchor_xy((w, h), (tw, th), "center-right")
    _text_with_shadow(img, (x, y), emoji, font,
                      color=(255, 255, 255, 255),
                      shadow=(0, 0, 0, 200), blur=10)
    ImageDraw.Draw(img).text((x, y), emoji, font=font, fill=(255, 255, 255, 255))


def _draw_arrow_highlight(
    img: Image.Image, *,
    text: str,
    anchor: str,
    frame: tuple[int, int],
    primary, accent, background, foreground,
    font,
) -> None:
    label = text or "Olha aqui"
    tw, th = _measure(label, font)
    arrow_w, arrow_h = 36, 36
    pad_x, pad_y = 16, 10
    bw = arrow_w + 8 + tw + pad_x * 2
    bh = max(th + pad_y * 2, arrow_h + 4)
    x, y = _anchor_xy(frame, (bw, bh), anchor)
    _rounded_rect(
        img, (x, y, x + bw, y + bh),
        radius=12,
        fill=_with_alpha(background, 200),
        border_color=_with_alpha(accent, 110),
        border_width=1,
    )
    # Arrow triangle pointing left
    ax = x + pad_x
    ay = y + bh // 2
    ImageDraw.Draw(img).polygon(
        [(ax, ay - arrow_h // 2), (ax + arrow_w, ay), (ax, ay + arrow_h // 2)],
        fill=accent,
    )
    ImageDraw.Draw(img).text(
        (ax + arrow_w + 10, y + (bh - th) // 2),
        label, font=font, fill=foreground,
    )


# --- anchor placement (mirrors CSS .anchor-* classes) ----------------------

def _anchor_xy(
    frame: tuple[int, int], box: tuple[int, int], anchor: str,
) -> tuple[int, int]:
    fw, fh = frame
    bw, bh = box
    if anchor == "center":
        return ((fw - bw) // 2, (fh - bh) // 2)
    if anchor == "top":
        return ((fw - bw) // 2, int(fh * 0.08))
    if anchor == "bottom":
        return ((fw - bw) // 2, fh - bh - int(fh * 0.14))
    if anchor == "bottom-left":
        return (int(fw * 0.06), fh - bh - int(fh * 0.14))
    if anchor == "center-right":
        return (fw - bw - int(fw * 0.08), int(fh * 0.36))
    return ((fw - bw) // 2, (fh - bh) // 2)
