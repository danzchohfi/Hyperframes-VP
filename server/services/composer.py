"""Generate a Hyperframes composition (HTML) from project artifacts.

Layout (vertical 1080x1920 by default; horizontal swaps to 1920x1080):

  [0.0 - intro_dur]   intro card (brand title + subtitle, palette glow)
  [intro_dur - main_end] main video with caption overlay (word-level highlight)
  [main_end - end]    outro card (brand outro_text + logo if any)
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from .brand import BrandBook

INTRO_DUR = 2.5
OUTRO_DUR = 2.0
MAX_LINE_CHARS = 28


def _group_words_into_lines(words: list[dict]) -> list[dict]:
    """Group word objects into caption lines preserving timing.

    Breaks on, in priority order:
      1. Speaker changes (lines never span two speakers).
      2. Sentence boundaries — punctuation `.`, `!`, `?`, `…` at end of
         the previous word.
      3. Long pauses — gap between words > 0.6s (≈ a natural breath).
      4. Length cap — MAX_LINE_CHARS to keep lines readable.

    The result is captions that read like spoken sentences instead of
    arbitrary 28-char chunks, which makes the kinetic word-pop animation
    feel like punctuation rather than ticker tape.
    """
    SENTENCE_END = (".", "!", "?", "…")
    PAUSE_GAP = 0.6  # seconds — anything bigger reads as a sentence break

    lines: list[dict] = []
    cur: list[dict] = []
    cur_len = 0
    cur_speaker: str | None | object = object()
    prev_end: float = 0.0
    prev_word_text: str = ""
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        wsp = w.get("_speaker")
        start = float(w["start"])
        speaker_changed = cur and cur_speaker is not object() and wsp != cur_speaker
        sentence_ended = bool(cur) and prev_word_text.endswith(SENTENCE_END)
        long_pause = bool(cur) and (start - prev_end) > PAUSE_GAP
        over_cap = cur and cur_len + 1 + len(text) > MAX_LINE_CHARS
        if cur and (speaker_changed or sentence_ended or long_pause or over_cap):
            lines.append(_finalize_line(cur, cur_speaker if cur_speaker is not object() else None))
            cur, cur_len = [], 0
        cur.append({"word": text, "start": start, "end": float(w["end"])})
        cur_len += (1 if cur_len else 0) + len(text)
        cur_speaker = wsp
        prev_end = float(w["end"])
        prev_word_text = text
    if cur:
        lines.append(_finalize_line(cur, cur_speaker if cur_speaker is not object() else None))
    return lines


def _finalize_line(words: list[dict], speaker: str | None = None) -> dict:
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
        "text": " ".join(w["word"] for w in words),
        "speaker": speaker,
    }


def _find_speaker(turns: list[dict] | None, t: float) -> str | None:
    if not turns:
        return None
    for turn in turns:
        if t >= float(turn.get("start") or 0.0) and t <= float(turn.get("end") or 0.0):
            return turn.get("speaker")
    return None


def _aspect_dims(aspect: str) -> tuple[int, int]:
    return {
        "9:16": (1080, 1920),
        "16:9": (1920, 1080),
        "1:1": (1080, 1080),
    }.get(aspect, (1080, 1920))


def build_composition(
    *,
    project_dir: Path,
    video_path: Path,
    video_duration: float,
    transcript: dict | None,
    brand: BrandBook,
    aspect: str = "9:16",
    chapters: list[dict] | None = None,  # if set → "Eddie cut" mode
    speaker_turns: list[dict] | None = None,  # [{speaker, start, end}, ...]
    animations: list[dict] | None = None,  # reels animations to overlay
    extra_blocks: list[dict] | None = None,  # registry-installed blocks scheduled on the timeline
) -> Path:
    """Materialize a Hyperframes project at project_dir/composition. Returns path."""
    comp_dir = project_dir / "composition"
    if comp_dir.exists():
        shutil.rmtree(comp_dir)
    comp_dir.mkdir(parents=True)

    # Bring along the local GSAP we vendored at the repo root so renders work offline.
    repo_root = Path(__file__).resolve().parents[2]
    vendor_src = repo_root / "vendor" / "gsap.min.js"
    vendor_dst = comp_dir / "vendor"
    vendor_dst.mkdir()
    if vendor_src.exists():
        shutil.copy2(vendor_src, vendor_dst / "gsap.min.js")
        gsap_src = "vendor/gsap.min.js"
    else:
        gsap_src = "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"

    # Copy the body video into the composition so renders can resolve it via relative path.
    body_video = comp_dir / "body.mp4"
    shutil.copy2(video_path, body_video)

    width, height = _aspect_dims(aspect)
    main_dur = max(video_duration, 0.1)
    # Intro/outro cards are opt-in. brand.name defaults to "Brand" so we
    # explicitly require intro_title (or outro_text) — otherwise every
    # render would get a random "Brand" splash that nobody asked for.
    intro_dur = INTRO_DUR if brand.intro_title else 0.0
    outro_dur = OUTRO_DUR if brand.outro_text else 0.0
    total_dur = round(intro_dur + main_dur + outro_dur, 3)

    # Captions inside the main segment, time-shifted by intro_dur.
    caption_lines: list[dict] = []
    if transcript and transcript.get("words"):
        words_with_speaker = []
        for w in transcript["words"]:
            mid = (float(w["start"]) + float(w["end"])) / 2.0
            words_with_speaker.append({**w, "_speaker": _find_speaker(speaker_turns, mid)})
        for line in _group_words_into_lines(words_with_speaker):
            speaker = line.get("speaker")
            shifted = {
                "start": round(line["start"] + intro_dur, 3),
                "duration": round(max(line["end"] - line["start"], 0.4), 3),
                "text": line["text"],
                "speaker": speaker,
                "words": [
                    {
                        "word": w["word"],
                        "start": round(w["start"] + intro_dur, 3),
                        "end": round(w["end"] + intro_dur, 3),
                    }
                    for w in line["words"]
                ],
            }
            caption_lines.append(shifted)

    # Project metadata
    (comp_dir / "meta.json").write_text(
        json.dumps({"id": project_dir.name, "name": brand.name or project_dir.name}, indent=2)
    )
    (comp_dir / "hyperframes.json").write_text(
        json.dumps(
            {
                "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
                "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
                "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
            },
            indent=2,
        )
    )
    (comp_dir / "package.json").write_text(
        json.dumps(
            {
                "name": project_dir.name.lower(),
                "private": True,
                "type": "module",
                "scripts": {
                    "dev": "npx --yes hyperframes@0.5.5 preview",
                    "check": "npx --yes hyperframes@0.5.5 lint && npx --yes hyperframes@0.5.5 inspect",
                    "render": "npx --yes hyperframes@0.5.5 render",
                },
            },
            indent=2,
        )
    )

    palette = brand.palette
    typo = brand.typography
    caption_color = brand.caption_color or palette.foreground
    caption_highlight = brand.caption_highlight or palette.accent

    intro_title = html.escape(brand.intro_title or "")
    intro_subtitle = html.escape(brand.intro_subtitle or (brand.tagline or ""))
    outro_text = html.escape(brand.outro_text or "")

    # Per-speaker color map (from BrandBook or auto-derived)
    speaker_colors: dict[str, str] = {}
    auto_palette = [palette.foreground, palette.accent, palette.primary, palette.secondary]
    seen_speakers: list[str] = []
    for ln in caption_lines:
        sp = ln.get("speaker")
        if sp and sp not in seen_speakers:
            seen_speakers.append(sp)
    for i, sp in enumerate(seen_speakers):
        # explicit brand override wins; else cycle through the auto palette
        if brand.speakers and sp in brand.speakers:
            speaker_colors[sp] = brand.speakers[sp].color
        else:
            speaker_colors[sp] = auto_palette[i % len(auto_palette)]
    speaker_names: dict[str, str] = {}
    for sp in seen_speakers:
        if brand.speakers and sp in brand.speakers:
            speaker_names[sp] = brand.speakers[sp].name
        else:
            speaker_names[sp] = f"Speaker {sp}"

    # Build caption HTML. Each word gets a `.cw-bg` span behind it so we
    # can draw an animated underline (scaleX 0→1) during the word, and the
    # word text itself sits inside `.cw-text` so we can tween color/scale
    # via the .hot CSS class without fighting GSAP's transform.
    # Words ending in `!` / `?` get `.cw--emph` so the timeline can give
    # them a bigger pop — emphasis is a tonal cue an editor would call out.
    caption_html_parts: list[str] = []
    for i, line in enumerate(caption_lines):
        def _word_span(w):
            word = w["word"]
            emph = " cw--emph" if word.rstrip().endswith(("!", "?")) else ""
            return (
                f'<span class="cw{emph}" data-w-start="{w["start"]}" data-w-end="{w["end"]}">'
                f'<span class="cw-bg"></span>'
                f'<span class="cw-text">{html.escape(word)}</span>'
                f'</span>'
            )
        word_spans = " ".join(_word_span(w) for w in line["words"])
        sp = line.get("speaker")
        sp_class = f' data-speaker="{html.escape(sp)}"' if sp else ""
        label_html = ""
        if sp and brand.speakers:
            label_html = f'<span class="cs-label">{html.escape(speaker_names.get(sp, "?"))}</span>'
        caption_html_parts.append(
            f'<div class="caption clip" id="cap{i}"{sp_class} '
            f'data-start="{line["start"]}" data-duration="{line["duration"]}" '
            f'data-track-index="3">{label_html}{word_spans}</div>'
        )
    caption_html = "\n      ".join(caption_html_parts)

    # Logo: copy into composition if it's a local file path
    logo_html = ""
    if brand.logo and brand.logo.enabled and brand.logo_url:
        logo_src = brand.logo_url
        # if it's a path on the server, try to copy locally
        if logo_src.startswith("/api/projects/"):
            # split /api/projects/{pid}/files/{name} → grab the filename
            try:
                local = project_dir / logo_src.split("/files/")[-1]
                if local.exists():
                    dst = comp_dir / f"logo{local.suffix}"
                    shutil.copy2(local, dst)
                    logo_src = dst.name
            except Exception:
                pass
        pos_map = {
            "top-left": "top: 4%; left: 4%;",
            "top-right": "top: 4%; right: 4%;",
            "bottom-left": "bottom: 4%; left: 4%;",
            "bottom-right": "bottom: 4%; right: 4%;",
        }
        pos_css = pos_map.get(brand.logo.position, pos_map["top-right"])
        logo_html = (
            f'<img id="brand-logo" src="{html.escape(logo_src)}" '
            f'style="position:absolute;{pos_css}width:{int(width * brand.logo.size)}px;'
            # opacity 0 so the GSAP entrance can tween it in.
            f'opacity:0;pointer-events:none;'
            f'filter:drop-shadow(0 2px 12px rgba(0,0,0,0.55));z-index:5;'
            f'will-change:transform,opacity"/>'
        )

    # Lower-thirds: pinned chip showing current speaker name + role.
    lower_thirds_html = ""
    if brand.lower_thirds and speaker_turns and brand.speakers:
        chips: list[str] = []
        for i, t in enumerate(speaker_turns):
            sp = t.get("speaker")
            style = brand.speakers.get(sp) if sp else None
            if not style:
                continue
            start_tl = float(t.get("start") or 0.0) + intro_dur
            dur_tl = max(0.3, float(t.get("end") or 0.0) - float(t.get("start") or 0.0))
            role_str = style.role or ""
            role_html = f'<div class="lt-role">{html.escape(role_str)}</div>' if role_str else ""
            chips.append(
                f'<div class="lt-chip clip" id="lt{i}" '
                f'data-start="{start_tl:.3f}" data-duration="{dur_tl:.3f}" '
                f'data-track-index="4" '
                f'style="--lt-color: {style.color};">'
                # Underline draws on entry via GSAP scaleX 0→1, then
                # collapses on exit.
                f'<div class="lt-accent"></div>'
                f'<div class="lt-name">{html.escape(style.name)}</div>'
                f'{role_html}'
                f'</div>'
            )
        lower_thirds_html = "\n      ".join(chips)

    # CTA cards
    cta_html = ""
    if brand.ctas:
        parts: list[str] = []
        for i, c in enumerate(brand.ctas):
            if not c.text:
                continue
            start_tl = float(c.start) + intro_dur
            dur_tl = max(0.5, float(c.duration))
            pos_css = {
                "top": "top: 8%;",
                "bottom": "bottom: 8%;",
                "center": "top: 50%; transform: translate(-50%, -50%);",
            }.get(c.position, "bottom: 8%;")
            sub = f'<div class="cta-sub">{html.escape(c.sub)}</div>' if c.sub else ""
            parts.append(
                f'<div class="cta-card clip" id="cta{i}" '
                f'data-start="{start_tl:.3f}" data-duration="{dur_tl:.3f}" '
                # Track 7 to avoid colliding with auto chapter cards on
                # track 6 — CTAs are deliberately above chapter cards in
                # visual stack and on the lint timeline.
                f'data-track-index="7" '
                f'style="{pos_css}">'
                f'<div class="cta-text">{html.escape(c.text)}</div>{sub}'
                f'</div>'
            )
        cta_html = "\n      ".join(parts)

    # Speaker-color CSS overrides
    speaker_css = ""
    if speaker_colors:
        speaker_css = "\n".join(
            f'.caption[data-speaker="{sp}"] {{ color: {col}; }} '
            f'.caption[data-speaker="{sp}"] .cs-label {{ color: {col}; border-color: {col}55; }}'
            for sp, col in speaker_colors.items()
        )

    # Build segments
    intro_html = ""
    if intro_dur > 0:
        intro_html = f"""
      <div id="intro" class="card clip" data-start="0" data-duration="{intro_dur}" data-track-index="2">
        <div class="card-bg"></div>
        <div class="card-inner">
          <h1 class="card-title">{intro_title}</h1>
          {f'<p class="card-sub">{intro_subtitle}</p>' if intro_subtitle else ''}
        </div>
      </div>"""

    outro_html = ""
    if outro_dur > 0:
        outro_html = f"""
      <div id="outro" class="card clip" data-start="{intro_dur + main_dur}" data-duration="{outro_dur}" data-track-index="2">
        <div class="card-bg"></div>
        <div class="card-inner">
          <h2 class="card-outro">{outro_text}</h2>
        </div>
      </div>"""

    # Registry-installed blocks scheduled on the timeline. Each block is
    # a full HTML composition (compositions/<name>.html written by
    # `hyperframes add`). We mount them as iframes positioned absolutely
    # so the framework's `class="clip"` + data-start/data-duration drives
    # visibility from the master timeline. Iframe content runs its own
    # internal GSAP timeline (auto-plays on load), so the block animates
    # as soon as it becomes visible. Not frame-accurate under scrubbing,
    # but fine for linear playback (the preview's primary mode).
    extra_blocks_html = ""
    if extra_blocks:
        parts: list[str] = []
        for i, eb in enumerate(extra_blocks):
            name = (eb.get("name") or "").strip()
            if not name:
                continue
            # Block file is written by `hyperframes add` to compositions/<name>.html.
            # If it's missing (uninstalled / never installed), skip silently —
            # the rest of the composition still renders.
            block_path = comp_dir / "compositions" / f"{name}.html"
            if not block_path.exists():
                continue
            start = max(0.0, float(eb.get("start") or 0.0))
            dur = max(0.5, float(eb.get("duration") or 5.0))
            # Each block has natural dimensions; we scale to fill the
            # composition (object-fit-like via iframe sizing). Allow per-
            # block opacity / blend so transitions feel less like a paste.
            opacity = max(0.0, min(1.0, float(eb.get("opacity") if eb.get("opacity") is not None else 1.0)))
            parts.append(
                f'<iframe class="clip extra-block" '
                f'data-start="{start:.3f}" data-duration="{dur:.3f}" '
                f'data-track-index="{60 + i}" '
                f'data-block-name="{html.escape(name)}" '
                f'src="compositions/{html.escape(name)}.html" '
                f'style="position:absolute;inset:0;width:100%;height:100%;'
                f'border:0;opacity:{opacity};pointer-events:none;background:transparent"></iframe>'
            )
        extra_blocks_html = "\n      ".join(parts)

    # Reels animations — overlays that fire at user-chosen / AI-chosen moments.
    animations_css = ""
    animations_html = ""
    animations_js = ""
    animations_audio_html = ""
    if animations:
        from . import reels_animations as ra
        normalized = [ra.normalize_animation(a, main_dur) for a in animations]
        # Resolve a local logo path so hook_card/cta_end variants can show it
        # offline during render. The composer already copies the brand logo
        # for the bug-style overlay; reuse the same target.
        anim_logo_src: str | None = None
        if brand.logo_url:
            src = brand.logo_url
            if src.startswith("/api/projects/"):
                try:
                    local = project_dir / src.split("/files/")[-1]
                    if local.exists():
                        dst = comp_dir / f"logo{local.suffix}"
                        if not dst.exists():
                            shutil.copy2(local, dst)
                        anim_logo_src = dst.name
                except Exception:
                    anim_logo_src = None
            else:
                anim_logo_src = src
        payload = ra.build_animations_payload(
            brand, normalized, width,
            intro_offset=intro_dur, logo_src=anim_logo_src,
        )
        animations_css = payload["css"]
        animations_html = payload["html"]
        animations_js = payload["js"]
        # SFX + TTS as Hyperframes <audio> clips. Copy each unique SFX
        # preset once into composition/sfx/, copy any project-level TTS
        # MP3s alongside. Tracks 30+ are reserved for these so they don't
        # collide with body audio (track 0) or overlay tracks (20+).
        sfx_audio_parts: list[str] = []
        sfx_dest_dir = comp_dir / "sfx"
        from . import sfx_lib  # local import to avoid hard dep when no anims
        copied_sfx: dict[str, str] = {}  # preset → relative path
        for i, anim in enumerate(normalized):
            sfx_name = ra.resolve_default_sfx(anim)
            if sfx_name and sfx_name in sfx_lib.SFX_RECIPES:
                p = sfx_lib.get_path_if_built(sfx_name)
                if p:
                    if sfx_name not in copied_sfx:
                        sfx_dest_dir.mkdir(exist_ok=True)
                        dst = sfx_dest_dir / p.name
                        if not dst.exists():
                            shutil.copy2(p, dst)
                        copied_sfx[sfx_name] = f"sfx/{p.name}"
                    rel = copied_sfx[sfx_name]
                    vol = float(anim.get("sfx_volume") or 0.7)
                    sfx_start = round(float(anim["start"]) + intro_dur, 3)
                    sfx_audio_parts.append(
                        f'<audio class="clip" '
                        f'data-start="{sfx_start}" '
                        f'data-duration="1.5" '
                        f'data-track-index="{30 + i}" '
                        f'data-volume="{vol:.2f}" '
                        f'src="{rel}" preload="auto"></audio>'
                    )
            # TTS narration: project_dir/.tts/{hash}.mp3 — copy to composition.
            tts_text = anim.get("tts")
            if tts_text:
                try:
                    from . import tts as tts_svc
                    tts_path = tts_svc.cache_path(
                        project_dir, tts_text,
                        anim.get("tts_voice") or "alloy",
                        float(anim.get("tts_speed") or 1.0),
                    )
                    if tts_path.exists():
                        tts_dest_dir = comp_dir / "tts"
                        tts_dest_dir.mkdir(exist_ok=True)
                        dst = tts_dest_dir / tts_path.name
                        if not dst.exists():
                            shutil.copy2(tts_path, dst)
                        vol = float(anim.get("tts_volume") or 1.0)
                        tts_start = round(float(anim["start"]) + intro_dur, 3)
                        sfx_audio_parts.append(
                            f'<audio class="clip" '
                            f'data-start="{tts_start}" '
                            f'data-duration="{float(anim["duration"]):.2f}" '
                            f'data-track-index="{40 + i}" '
                            f'data-volume="{vol:.2f}" '
                            f'src="tts/{tts_path.name}" preload="auto"></audio>'
                        )
                except Exception:
                    pass
        animations_audio_html = "\n      ".join(sfx_audio_parts)

    main_start = intro_dur

    # Chapter overlays ("Eddie cut" mode). Each chapter shows a flash card with
    # its name + summary for ~1.6s at the chapter boundary on the timeline.
    chapter_html = ""
    # Skip chapter cards entirely on short clips (reels, hooks, etc.) —
    # a full-frame cinematic card eating 1.8s of a 20s reel reads as
    # an interruption, not a transition. Same logic: skip if the user
    # only got 1 chapter back from the detector — that's "the whole
    # video is one topic", not a structure worth signposting.
    if chapters and main_dur >= 45.0 and len(chapters) >= 2:
        cards = []
        for i, ch in enumerate(chapters):
            ch_start = float(ch.get("start", 0.0)) + intro_dur
            # Hold a touch longer so the cinematic in/out doesn't feel rushed.
            ch_dur = min(2.0, float(ch.get("duration") or 1.8))
            cards.append(
                f'<div id="ch{i}" class="chapter-card clip" '
                f'data-start="{ch_start:.3f}" data-duration="{ch_dur:.3f}" data-track-index="6">'
                # Vignette overlay paints the frame for cinematic feel.
                f'<div class="ch-veil"></div>'
                f'<div class="ch-inner">'
                f'<div class="ch-num">CHAPTER {i + 1:02d}</div>'
                f'<div class="ch-name">{html.escape(ch.get("name", ""))}</div>'
                f'</div>'
                f'</div>'
            )
        chapter_html = "\n      ".join(cards)

    html_doc = f"""<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={width}, height={height}" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600;700&display=swap" rel="stylesheet" />
    <script src="{gsap_src}"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{
        width: {width}px; height: {height}px; overflow: hidden;
        background: {palette.background};
        font-family: "{typo.body_family}", system-ui, sans-serif;
        color: {palette.foreground};
        -webkit-font-smoothing: antialiased;
      }}
      #root {{ position: relative; width: {width}px; height: {height}px; overflow: hidden; }}
      #body-video {{
        position: absolute; inset: 0;
        width: {width}px; height: {height}px;
        object-fit: cover;
        background: {palette.background};
      }}
      .card {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; text-align: center; }}
      .card-bg {{
        position: absolute; inset: 0;
        background:
          radial-gradient(ellipse 70% 55% at 50% 50%, {palette.primary}33, transparent 65%),
          radial-gradient(ellipse 55% 40% at 50% 75%, {palette.secondary}1a, transparent 70%),
          {palette.background};
      }}
      .card-inner {{ position: relative; padding: 0 6%; }}
      .card-title {{
        font-family: "{typo.title_family}", system-ui, sans-serif;
        font-weight: {typo.title_weight};
        font-size: {int(width * 0.10)}px;
        letter-spacing: -0.03em;
        line-height: 1.05;
        background: linear-gradient(180deg, {palette.foreground} 0%, {palette.primary} 100%);
        -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
        text-shadow: 0 0 60px {palette.primary}55;
      }}
      .card-sub {{
        margin-top: {int(height * 0.025)}px;
        font-weight: 300;
        font-size: {int(width * 0.030)}px;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: {palette.foreground}cc;
      }}
      .card-outro {{
        font-family: "{typo.title_family}", system-ui, sans-serif;
        font-weight: {typo.title_weight};
        font-size: {int(width * 0.07)}px;
        letter-spacing: -0.02em;
        color: {palette.foreground};
        text-shadow: 0 0 40px {palette.accent}66;
      }}
      .caption {{
        position: absolute;
        left: 6%; right: 6%;
        {'bottom: 12%;' if brand.caption_position == 'bottom' else 'top: 50%; transform: translateY(-50%);'}
        text-align: center;
        font-family: "{typo.title_family}", system-ui, sans-serif;
        font-weight: {('900' if brand.caption_style == 'tiktok' else '600' if brand.caption_style == 'podcast' else '700')};
        font-size: {int(width * (0.07 if brand.caption_style == 'tiktok' else 0.045 if brand.caption_style == 'podcast' else 0.055))}px;
        line-height: {('1.05' if brand.caption_style == 'tiktok' else '1.25' if brand.caption_style == 'podcast' else '1.15')};
        color: {caption_color};
        letter-spacing: {'-0.02em' if brand.caption_style == 'tiktok' else '0em' if brand.caption_style == 'podcast' else '-0.01em'};
        text-transform: {('uppercase' if brand.caption_style == 'tiktok' else 'none')};
        text-shadow: {('0 4px 28px rgba(0,0,0,0.95), 2px 2px 0 #000, -2px 2px 0 #000, 2px -2px 0 #000, -2px -2px 0 #000' if brand.caption_style == 'tiktok' else '0 2px 14px rgba(0,0,0,0.7)' if brand.caption_style == 'podcast' else '0 4px 28px rgba(0,0,0,0.85), 0 0 2px rgba(0,0,0,0.6)')};
        {'background: rgba(0,0,0,0.55); padding: 14px 20px; border-radius: 10px; backdrop-filter: blur(8px);' if brand.caption_style == 'podcast' else ''}
      }}
      .caption .cw {{
        position: relative;
        display: inline-block;
        margin: 0 0.18em;
        /* hidden until GSAP entrance tween brings them in */
        opacity: 0;
        transform: translateY(14px) scale(0.92);
        will-change: transform, opacity;
      }}
      .caption .cw-text {{
        position: relative;
        z-index: 2;
        display: inline-block;
        transition: color 80ms linear, transform 110ms cubic-bezier(.34,1.56,.64,1), text-shadow 80ms linear;
      }}
      .caption .cw-bg {{
        position: absolute;
        left: -0.12em;
        right: -0.12em;
        bottom: -0.04em;
        height: {('0.42em' if brand.caption_style == 'tiktok' else '0.22em')};
        background: linear-gradient(90deg,
          {caption_highlight} 0%,
          {palette.accent} 100%);
        opacity: 0.85;
        border-radius: 4px;
        transform: scaleX(0);
        transform-origin: left center;
        z-index: 1;
        will-change: transform;
      }}
      .caption .cw.hot .cw-text {{
        color: {caption_highlight};
        text-shadow: 0 0 24px {caption_highlight}cc, 0 4px 28px rgba(0,0,0,0.92);
        transform: scale({'1.20' if brand.caption_style == 'tiktok' else '1.10'});
      }}
      /* Emphasis words (ending in ! or ?) pop a touch harder and the
         underline saturates more. Subtle but reads as "voice raised". */
      .caption .cw--emph.hot .cw-text {{
        transform: scale({'1.32' if brand.caption_style == 'tiktok' else '1.20'});
      }}
      .caption .cw--emph .cw-bg {{
        opacity: 1.0;
        filter: brightness(1.15);
      }}
      .cs-label {{
        display: inline-block;
        font-size: {int(width * 0.022)}px;
        font-weight: 700;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        padding: 4px 10px;
        margin-right: 10px;
        border: 1px solid rgba(255,255,255,0.25);
        border-radius: 999px;
        vertical-align: middle;
      }}
      {speaker_css}
      .lt-chip {{
        position: absolute;
        left: 4%;
        bottom: 18%;
        padding: 14px 22px 12px 22px;
        background: linear-gradient(120deg,
          rgba(0,0,0,0.72) 0%,
          rgba(0,0,0,0.45) 60%,
          rgba(0,0,0,0.30) 100%);
        border-radius: 10px;
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        max-width: 64%;
        box-shadow: 0 12px 38px rgba(0,0,0,0.45);
        overflow: hidden;
      }}
      /* Accent underline that draws on entry (scaleX 0->1) and collapses
         on exit. transform-origin left so the wipe reads as a confident
         broadcaster stinger rather than a wobble. */
      .lt-chip .lt-accent {{
        position: absolute;
        left: 0;
        bottom: 0;
        height: 3px;
        width: 100%;
        background: linear-gradient(90deg,
          var(--lt-color, {palette.accent}) 0%,
          {palette.accent} 100%);
        transform: scaleX(0);
        transform-origin: left center;
      }}
      .lt-chip .lt-name {{
        font-family: "{typo.title_family}", sans-serif;
        font-weight: 800;
        font-size: {int(width * 0.030)}px;
        color: var(--lt-color, {palette.foreground});
        letter-spacing: -0.01em;
        line-height: 1.1;
      }}
      .lt-chip .lt-role {{
        font-size: {int(width * 0.018)}px;
        font-weight: 500;
        color: rgba(255,255,255,0.82);
        margin-top: 4px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }}
      .cta-card {{
        position: absolute;
        left: 6%;
        right: 6%;
        padding: 18px 24px;
        background: linear-gradient(135deg, {palette.primary}ee, {palette.accent}ee);
        color: #0a0b10;
        border-radius: 14px;
        box-shadow: 0 18px 60px rgba(0,0,0,0.55);
        text-align: center;
      }}
      .cta-card .cta-text {{
        font-family: "{typo.title_family}", sans-serif;
        font-weight: 800;
        font-size: {int(width * 0.046)}px;
        letter-spacing: -0.02em;
      }}
      .cta-card .cta-sub {{
        font-size: {int(width * 0.024)}px;
        font-weight: 500;
        opacity: 0.85;
        margin-top: 4px;
      }}
      /* Cinematic chapter card: full-frame vignette behind the title so
         the cut between chapters reads as a deliberate "next act"
         transition, not a sticker. */
      .chapter-card {{
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: flex-start;
        padding-left: 8%;
        padding-right: 8%;
      }}
      .chapter-card .ch-veil {{
        position: absolute;
        inset: 0;
        background: radial-gradient(ellipse at 30% 50%,
          {palette.background}aa 0%,
          {palette.background}cc 60%,
          {palette.background}ee 100%);
        opacity: 0;
      }}
      .chapter-card .ch-inner {{
        position: relative;
        max-width: 80%;
      }}
      .chapter-card .ch-num {{
        display: inline-block;
        font-size: {int(width * 0.020)}px;
        font-weight: 800;
        letter-spacing: 0.42em;
        color: {palette.primary};
        text-transform: uppercase;
        padding: 6px 14px;
        margin-bottom: 14px;
        border: 1px solid {palette.primary}80;
        border-radius: 999px;
      }}
      .chapter-card .ch-name {{
        font-family: "{typo.title_family}", system-ui, sans-serif;
        font-size: {int(width * 0.064)}px;
        font-weight: {typo.title_weight};
        color: {palette.foreground};
        letter-spacing: -0.02em;
        line-height: 1.05;
        text-shadow: 0 4px 38px rgba(0,0,0,0.85);
      }}
      {animations_css}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total_dur}"
         data-width="{width}" data-height="{height}">

      <video id="body-video" class="clip"
             data-start="{main_start}" data-duration="{main_dur}" data-track-index="1"
             data-volume="0"
             src="body.mp4" muted preload="auto" playsinline></video>
      <audio id="body-audio" class="clip"
             data-start="{main_start}" data-duration="{main_dur}" data-track-index="0"
             data-volume="1.0"
             src="body.mp4" preload="auto"></audio>
      {intro_html}
      {chapter_html}
      {caption_html}
      {lower_thirds_html}
      {cta_html}
      {logo_html}
      {extra_blocks_html}
      {animations_html}
      {animations_audio_html}
      {outro_html}
    </div>

    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true, defaults: {{ ease: "power3.out" }} }});

      const intro = document.getElementById("intro");
      if (intro) {{
        tl.fromTo(intro.querySelector(".card-title"), {{ opacity: 0, y: 24, filter: "blur(10px)" }},
                  {{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.9 }}, 0.05);
        const sub = intro.querySelector(".card-sub");
        if (sub) tl.fromTo(sub, {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: 0, duration: 0.7 }}, 0.45);
        tl.to(intro, {{ opacity: 0, duration: 0.4, ease: "power2.in" }}, {intro_dur} - 0.4);
      }}

      const outro = document.getElementById("outro");
      if (outro) {{
        const outroStart = {intro_dur + main_dur};
        tl.fromTo(outro.querySelector(".card-outro"), {{ opacity: 0, y: 18 }},
                  {{ opacity: 1, y: 0, duration: 0.8 }}, outroStart + 0.05);
      }}

      // Brand logo: confident entrance, subtle idle hover, sits at 0.92
      // opacity during the body so it stays brand-present without
      // stealing attention from the captions.
      const brandLogo = document.getElementById("brand-logo");
      if (brandLogo) {{
        tl.fromTo(brandLogo,
          {{ opacity: 0, scale: 0.6, y: -18 }},
          {{ opacity: 0.92, scale: 1, y: 0, duration: 0.7, ease: "back.out(2.2)" }},
          Math.max(0, {intro_dur} - 0.4)
        );
        // Looping idle bob during the body so the logo feels "alive"
        // without animating anything dramatic. Bounded repeat (no -1)
        // so Hyperframes' deterministic export knows the timeline
        // duration; yoyo plays it both ways on each scrub.
        const __bobReps = Math.max(1, Math.ceil({main_dur} / 4));
        tl.to(brandLogo,
          {{ y: -3, duration: 4, ease: "sine.inOut", yoyo: true, repeat: __bobReps }},
          {intro_dur} + 0.1
        );
        // Fade-out into the outro card so the logo doesn't fight the
        // CTA for attention.
        const outroStart2 = {intro_dur + main_dur};
        tl.to(brandLogo,
          {{ opacity: 0, scale: 0.95, duration: 0.5, ease: "power2.in" }},
          outroStart2
        );
      }}

      // Lower-thirds: broadcaster stinger — chip slides in, accent
      // underline draws across, name/role appear with a small stagger,
      // exit collapses vertically so it feels intentional, not abrupt.
      for (const lt of document.querySelectorAll(".lt-chip")) {{
        const start = parseFloat(lt.dataset.start);
        const dur = parseFloat(lt.dataset.duration);
        tl.fromTo(lt,
          {{ opacity: 0, x: -36, scaleY: 0.85 }},
          {{ opacity: 1, x: 0, scaleY: 1, duration: 0.5, ease: "power3.out", transformOrigin: "left bottom" }},
          start
        );
        const accent = lt.querySelector(".lt-accent");
        if (accent) {{
          tl.fromTo(accent,
            {{ scaleX: 0 }},
            {{ scaleX: 1, duration: 0.55, ease: "power2.out" }},
            start + 0.10
          );
        }}
        const name = lt.querySelector(".lt-name");
        if (name) tl.fromTo(name, {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.35 }}, start + 0.15);
        const role = lt.querySelector(".lt-role");
        if (role) tl.fromTo(role, {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.35 }}, start + 0.25);
        // Exit: collapse vertically + fade
        tl.to(lt,
          {{ opacity: 0, scaleY: 0.3, duration: 0.4, ease: "power2.in", transformOrigin: "left bottom" }},
          start + dur - 0.4
        );
      }}

      // CTA cards
      for (const cta of document.querySelectorAll(".cta-card")) {{
        const start = parseFloat(cta.dataset.start);
        const dur = parseFloat(cta.dataset.duration);
        tl.fromTo(cta, {{ opacity: 0, y: 30, scale: 0.96 }},
                       {{ opacity: 1, y: 0, scale: 1, duration: 0.5, ease: "back.out(1.6)" }}, start);
        tl.to(cta, {{ opacity: 0, y: 30, duration: 0.45, ease: "power2.in" }}, start + dur - 0.45);
      }}

      // Chapter cards — cinematic "next act" transition:
      //   1. Vignette darkens the frame so the title pops.
      //   2. Chapter number pill pops in (back.out scale).
      //   3. Chapter name slides up underneath with a small delay.
      //   4. Exit: zoom-blur out so the eye returns to the host.
      for (const card of document.querySelectorAll(".chapter-card")) {{
        const start = parseFloat(card.dataset.start);
        const dur = parseFloat(card.dataset.duration);
        const veil = card.querySelector(".ch-veil");
        const num = card.querySelector(".ch-num");
        const name = card.querySelector(".ch-name");
        if (veil) tl.fromTo(veil, {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: "power2.out" }}, start);
        if (num) {{
          tl.fromTo(num,
            {{ opacity: 0, scale: 0.6, y: 8 }},
            {{ opacity: 1, scale: 1, y: 0, duration: 0.45, ease: "back.out(2.4)" }},
            start + 0.10
          );
        }}
        if (name) {{
          tl.fromTo(name,
            {{ opacity: 0, y: 28, filter: "blur(8px)" }},
            {{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.55, ease: "power3.out" }},
            start + 0.25
          );
        }}
        // Exit: zoom + blur, vignette fades back to reveal the host.
        tl.to(card,
          {{ scale: 1.06, filter: "blur(6px)", opacity: 0, duration: 0.45, ease: "power2.in", transformOrigin: "30% 50%" }},
          start + dur - 0.45
        );
      }}

      {animations_js}

      // Per-word kinetic caption animation — entrance pop + underline draw
      // anchored on the main timeline so Hyperframes can scrub it
      // deterministically (no wall-clock dependency).
      const allCw = Array.from(document.querySelectorAll(".caption .cw"));
      for (const w of allCw) {{
        const s = parseFloat(w.dataset.wStart);
        const e = parseFloat(w.dataset.wEnd);
        if (!isFinite(s) || !isFinite(e) || e <= s) continue;
        const dur = Math.max(e - s, 0.06);
        const entrance = Math.max(0, s - 0.08);
        // Pop in just before the word is spoken
        tl.fromTo(w,
          {{ opacity: 0, y: 14, scale: 0.92 }},
          {{ opacity: 1, y: 0, scale: 1, duration: 0.22, ease: "back.out(2)" }},
          entrance
        );
        // Underline draws across the word as it's being spoken
        const bg = w.querySelector(".cw-bg");
        if (bg) {{
          tl.fromTo(bg,
            {{ scaleX: 0 }},
            {{ scaleX: 1, duration: Math.min(dur, 0.45), ease: "power2.out" }},
            s
          );
        }}
      }}

      window.__timelines["main"] = tl;

      // Hot toggle for color/shadow — CSS-driven, runs on every scrub tick.
      const allWords = allCw;
      function applyHotAt(t) {{
        for (const w of allWords) {{
          const s = parseFloat(w.dataset.wStart);
          const e = parseFloat(w.dataset.wEnd);
          w.classList.toggle("hot", t >= s && t <= e);
        }}
      }}
      window.addEventListener("hf-seek", (ev) => {{
        const t = (ev.detail && typeof ev.detail.time === "number") ? ev.detail.time : 0;
        applyHotAt(t);
      }});

      // Parent-controlled scrub when rendered inside the app's inline
      // iframe preview. Parent posts {{type:"seek", time}} and we drive
      // the timeline + hot-toggle so captions, animations and the body
      // video frame all match the parent's playhead. {{type:"hf-ready"}}
      // back so the parent can start its RAF only after the iframe has
      // mounted everything.
      window.addEventListener("message", (ev) => {{
        const data = ev.data;
        if (!data || typeof data !== "object") return;
        if (data.type === "seek" && typeof data.time === "number") {{
          try {{ tl.seek(data.time); }} catch (e) {{}}
          applyHotAt(data.time);
          window.dispatchEvent(new CustomEvent("hf-seek", {{ detail: {{ time: data.time }} }}));
        }}
      }});
      try {{
        const tlDur = tl.duration();
        parent.postMessage({{ type: "hf-ready", duration: tlDur }}, "*");
      }} catch (e) {{}}
    </script>
  </body>
</html>
"""

    (comp_dir / "index.html").write_text(html_doc)
    # Drop a hyperframes.json so the composition dir is a valid Hyperframes
    # project — required by `hyperframes add` / `lint` / `preview`. Mirrors
    # what `hyperframes init` writes; the `paths` keys tell the CLI where
    # community-installed blocks and components land relative to this dir.
    (comp_dir / "hyperframes.json").write_text(
        '{\n'
        '  "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",\n'
        '  "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",\n'
        '  "paths": {\n'
        '    "blocks": "compositions",\n'
        '    "components": "compositions/components",\n'
        '    "assets": "assets"\n'
        '  }\n'
        '}\n'
    )
    return comp_dir
