"""Build a .zip bundle with all the editor-relevant artifacts of a project.

Includes (when present):
  - source.mp4 / roughcut.mp4 / graded.mp4
  - exports/*.fcpxml, *.xml, *.srt, *.vtt, *.mp4
  - transcript.json, cuts.json, fillers.json, soundbites.json,
    story.json, roughcut.json, brand.json, broll_placement.json
  - meta.json + state.json (snapshot of project state)
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ARTIFACT_FILES = (
    "meta.json",
    "state.json",
    "transcript.json",
    "cuts.json",
    "fillers.json",
    "soundbites.json",
    "story.json",
    "roughcut.json",
    "brand.json",
    "broll_placement.json",
    "source_tags.json",
    "source.mp4",
    "graded.mp4",
    "roughcut.mp4",
    "lut.cube",
)


def build_bundle(project_dir: Path, dst_zip: Path, *, include_source: bool = True) -> int:
    """Returns the number of files added."""
    dst_zip.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_STORED) as zf:
        for name in ARTIFACT_FILES:
            if name == "source.mp4" and not include_source:
                continue
            f = project_dir / name
            if f.exists():
                zf.write(f, arcname=name)
                count += 1
        # exports/ — keep only generated XML / SRT / final MP4s
        ex = project_dir / "exports"
        if ex.exists():
            for f in ex.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() in (".xml", ".fcpxml", ".srt", ".vtt", ".mp4"):
                    zf.write(f, arcname=f"exports/{f.name}")
                    count += 1
        # angles/
        ang = project_dir / "angles"
        if ang.exists() and include_source:
            for f in ang.glob("*.mp4"):
                zf.write(f, arcname=f"angles/{f.name}")
                count += 1
    return count
