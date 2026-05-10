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
    """Group word objects into caption lines of <= MAX_LINE_CHARS, preserving timing."""
    lines: list[dict] = []
    cur: list[dict] = []
    cur_len = 0
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        if cur and cur_len + 1 + len(text) > MAX_LINE_CHARS:
            lines.append(_finalize_line(cur))
            cur, cur_len = [], 0
        cur.append({"word": text, "start": float(w["start"]), "end": float(w["end"])})
        cur_len += (1 if cur_len else 0) + len(text)
    if cur:
        lines.append(_finalize_line(cur))
    return lines


def _finalize_line(words: list[dict]) -> dict:
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
        "text": " ".join(w["word"] for w in words),
    }


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
        for line in _group_words_into_lines(transcript["words"]):
            shifted = {
                "start": round(line["start"] + intro_dur, 3),
                "duration": round(max(line["end"] - line["start"], 0.4), 3),
                "text": line["text"],
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

    # Build caption HTML
    caption_html_parts: list[str] = []
    for i, line in enumerate(caption_lines):
        word_spans = " ".join(
            f'<span class="cw" data-w-start="{w["start"]}" data-w-end="{w["end"]}">{html.escape(w["word"])}</span>'
            for w in line["words"]
        )
        caption_html_parts.append(
            f'<div class="caption clip" id="cap{i}" '
            f'data-start="{line["start"]}" data-duration="{line["duration"]}" '
            f'data-track-index="3">{word_spans}</div>'
        )
    caption_html = "\n      ".join(caption_html_parts)

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
        font-weight: 700;
        font-size: {int(width * 0.055)}px;
        line-height: 1.15;
        color: {caption_color};
        text-shadow: 0 4px 28px rgba(0,0,0,0.85), 0 0 2px rgba(0,0,0,0.6);
        letter-spacing: -0.01em;
      }}
      .caption .cw {{ display: inline-block; margin: 0 0.18em; transition: color 60ms linear; }}
      .caption .cw.hot {{ color: {caption_highlight}; text-shadow: 0 0 20px {caption_highlight}aa, 0 4px 28px rgba(0,0,0,0.85); }}
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
