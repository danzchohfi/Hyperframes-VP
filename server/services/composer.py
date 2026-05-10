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
    """Group word objects into caption lines of <= MAX_LINE_CHARS, preserving timing.

    If words carry a `_speaker` field, lines never span more than one speaker.
    """
    lines: list[dict] = []
    cur: list[dict] = []
    cur_len = 0
    cur_speaker: str | None | object = object()  # sentinel
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        wsp = w.get("_speaker")
        speaker_changed = cur and cur_speaker is not object() and wsp != cur_speaker
        if cur and (speaker_changed or cur_len + 1 + len(text) > MAX_LINE_CHARS):
            lines.append(_finalize_line(cur, cur_speaker if cur_speaker is not object() else None))
            cur, cur_len = [], 0
        cur.append({"word": text, "start": float(w["start"]), "end": float(w["end"])})
        cur_len += (1 if cur_len else 0) + len(text)
        cur_speaker = wsp
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
    intro_dur = INTRO_DUR if (brand.intro_title or brand.name) else 0.0
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

    intro_title = html.escape(brand.intro_title or brand.name)
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

    # Build caption HTML
    caption_html_parts: list[str] = []
    for i, line in enumerate(caption_lines):
        word_spans = " ".join(
            f'<span class="cw" data-w-start="{w["start"]}" data-w-end="{w["end"]}">{html.escape(w["word"])}</span>'
            for w in line["words"]
        )
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

    main_start = intro_dur

    # Chapter overlays ("Eddie cut" mode). Each chapter shows a flash card with
    # its name + summary for ~1.6s at the chapter boundary on the timeline.
    chapter_html = ""
    if chapters:
        cards = []
        for i, ch in enumerate(chapters):
            ch_start = float(ch.get("start", 0.0)) + intro_dur
            ch_dur = min(1.6, float(ch.get("duration") or 1.6))
            cards.append(
                f'<div id="ch{i}" class="chapter-card clip" '
                f'data-start="{ch_start:.3f}" data-duration="{ch_dur:.3f}" data-track-index="3">'
                f'<div class="ch-num">CHAPTER {i + 1:02d}</div>'
                f'<div class="ch-name">{html.escape(ch.get("name", ""))}</div>'
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
      .caption .cw {{ display: inline-block; margin: 0 0.18em; transition: color 60ms linear, transform 80ms ease; }}
      .caption .cw.hot {{
        color: {caption_highlight};
        text-shadow: 0 0 20px {caption_highlight}aa, 0 4px 28px rgba(0,0,0,0.85);
        {'transform: scale(1.15);' if brand.caption_style == 'tiktok' else ''}
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
      .chapter-card {{
        position: absolute;
        top: 8%; left: 6%;
        padding: 14px 22px 16px 22px;
        background: linear-gradient(135deg, {palette.background}cc, {palette.background}99);
        border: 1px solid {palette.primary}55;
        border-left: 3px solid {palette.primary};
        border-radius: 10px;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        box-shadow: 0 12px 40px rgba(0,0,0,0.45);
      }}
      .chapter-card .ch-num {{
        font-size: {int(width * 0.018)}px;
        font-weight: 700;
        letter-spacing: 0.32em;
        color: {palette.primary};
        text-transform: uppercase;
        margin-bottom: 4px;
      }}
      .chapter-card .ch-name {{
        font-family: "{typo.title_family}", system-ui, sans-serif;
        font-size: {int(width * 0.038)}px;
        font-weight: {typo.title_weight};
        color: {palette.foreground};
        letter-spacing: -0.02em;
      }}
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

      // Chapter card flash-ins
      for (const card of document.querySelectorAll(".chapter-card")) {{
        const start = parseFloat(card.dataset.start);
        const dur = parseFloat(card.dataset.duration);
        tl.fromTo(card, {{ opacity: 0, x: -24 }}, {{ opacity: 1, x: 0, duration: 0.45, ease: "power3.out" }}, start);
        tl.to(card, {{ opacity: 0, duration: 0.4, ease: "power2.in" }}, start + dur - 0.4);
      }}

      window.__timelines["main"] = tl;

      // Word-level caption highlighting driven by hf-seek time.
      const captions = Array.from(document.querySelectorAll(".caption"));
      const allWords = captions.flatMap(c => Array.from(c.querySelectorAll(".cw")));
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
    </script>
  </body>
</html>
"""

    (comp_dir / "index.html").write_text(html_doc)
    return comp_dir
