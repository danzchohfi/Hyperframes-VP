"""Generate FCPXML 1.10 from a project's source video + cut plan + transcript.

Final Cut Pro can import this via File → Import → XML. The exported timeline
contains one `<asset-clip>` per kept segment from `cuts.json`, with word-level
markers from the transcript so the editor can navigate by spoken word.

Multicam: when a project has additional angles registered in `angles.json`,
we emit a `<media>` with a `<multicam>` element grouping all angles, then place
`<mc-clip>` segments on the spine instead of `<asset-clip>`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .ffmpeg import probe


# --- frame-rate helpers -------------------------------------------------------

# (rate Hz, frame_duration string, format suffix, time_base)
_RATE_TABLE: list[tuple[float, str, str, int]] = [
    (23.976, "1001/24000s", "p2398", 24000),
    (24.0,   "100/2400s",   "p24",   24000),
    (25.0,   "100/2500s",   "p25",   25000),
    (29.97,  "1001/30000s", "p2997", 30000),
    (30.0,   "100/3000s",   "p30",   30000),
    (50.0,   "100/5000s",   "p50",   50000),
    (59.94,  "1001/60000s", "p5994", 60000),
    (60.0,   "100/6000s",   "p60",   60000),
]


def _pick_rate(num: int, den: int) -> tuple[str, str, int]:
    rate = (num / den) if den else 30.0
    best = min(_RATE_TABLE, key=lambda row: abs(row[0] - rate))
    if abs(best[0] - rate) > 0.5:
        return ("100/3000s", "p30", 30000)
    return (best[1], best[2], best[3])


def _t(seconds: float, tb: int) -> str:
    """Frame-aligned rational time."""
    if seconds <= 0:
        return "0s"
    ticks = round(seconds * tb)
    return f"{ticks}/{tb}s"


async def _video_meta(source: Path) -> dict[str, Any]:
    info = await probe(source)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    width = int(v.get("width") or 1920)
    height = int(v.get("height") or 1080)
    rf = (v.get("r_frame_rate") or "30/1").split("/")
    num = int(rf[0])
    den = int(rf[1]) if len(rf) > 1 and int(rf[1]) > 0 else 1
    fd, suffix, tb = _pick_rate(num, den)
    duration = float(info.get("format", {}).get("duration") or 0.0)
    return {
        "width": width,
        "height": height,
        "frame_duration": fd,
        "format_name": f"FFVideoFormat{height}{suffix}",
        "time_base": tb,
        "duration": duration,
    }


# --- builders -----------------------------------------------------------------

async def build_single_cam_fcpxml(
    *,
    project_name: str,
    source: Path,
    cuts: list[tuple[float, float]] | None,
    transcript: dict | None = None,
) -> str:
    meta = await _video_meta(source)
    tb = meta["time_base"]
    src_dur = meta["duration"] or 0.0

    fcpxml = ET.Element("fcpxml", {"version": "1.10"})

    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": meta["format_name"],
            "frameDuration": meta["frame_duration"],
            "width": str(meta["width"]),
            "height": str(meta["height"]),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )
    ET.SubElement(
        resources,
        "asset",
        {
            "id": "r2",
            "name": source.stem,
            "src": _file_url(source),
            "start": "0s",
            "duration": _t(src_dur, tb),
            "hasVideo": "1",
            "hasAudio": "1",
            "format": "r1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48000",
        },
    )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": f"HyperFrames · {project_name}"})
    project = ET.SubElement(event, "project", {"name": project_name})

    keep = cuts if cuts else [(0.0, src_dur)]
    timeline_dur = sum(e - s for s, e in keep)

    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": _t(timeline_dur, tb),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    offset = 0.0
    words = (transcript or {}).get("words") or []
    for i, (s, e) in enumerate(keep):
        clip_dur = max(e - s, 1.0 / 30.0)
        clip = ET.SubElement(
            spine,
            "asset-clip",
            {
                "ref": "r2",
                "name": f"{project_name} cut {i + 1}",
                "offset": _t(offset, tb),
                "start": _t(s, tb),
                "duration": _t(clip_dur, tb),
                "tcFormat": "NDF",
            },
        )
        for w in words:
            ws = float(w.get("start") or 0.0)
            we = float(w.get("end") or ws)
            if ws >= s and ws < e:
                ET.SubElement(
                    clip,
                    "marker",
                    {
                        "start": _t(ws, tb),
                        "duration": _t(max(we - ws, 1 / 30.0), tb),
                        "value": str(w.get("word", "")).strip(),
                    },
                )
        offset += clip_dur

    return _render(fcpxml)


async def build_multitrack_fcpxml(
    *,
    project_name: str,
    source: Path,
    cuts: list[tuple[float, float]] | None,
    transcript: dict | None = None,
    broll_angles: list[tuple[str, Path]] | None = None,
    broll_placements: list[dict] | None = None,
) -> str:
    """A-roll spine on V1, B-roll inserts as connected clips on lane=1.

    `broll_placements` items: {angle_index, angle_in, angle_out,
    timeline_offset, timeline_duration, soundbite_id?}.
    """
    meta = await _video_meta(source)
    tb = meta["time_base"]
    src_dur = meta["duration"] or 0.0
    broll_angles = broll_angles or []
    broll_placements = broll_placements or []

    fcpxml = ET.Element("fcpxml", {"version": "1.10"})
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": meta["format_name"],
            "frameDuration": meta["frame_duration"],
            "width": str(meta["width"]),
            "height": str(meta["height"]),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )
    # A-roll asset
    ET.SubElement(
        resources,
        "asset",
        {
            "id": "r2",
            "name": source.stem,
            "src": _file_url(source),
            "start": "0s",
            "duration": _t(src_dur, tb),
            "hasVideo": "1",
            "hasAudio": "1",
            "format": "r1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48000",
        },
    )
    # B-roll assets
    angle_durations: dict[int, float] = {}
    for i, (name, path) in enumerate(broll_angles):
        m = await _video_meta(path)
        angle_durations[i] = m["duration"] or 0.0
        ET.SubElement(
            resources,
            "asset",
            {
                "id": f"b{i + 1}",
                "name": name,
                "src": _file_url(path),
                "start": "0s",
                "duration": _t(m["duration"] or 0.0, tb),
                "hasVideo": "1",
                "hasAudio": "1",
                "format": "r1",
                "audioSources": "1",
                "audioChannels": "2",
                "audioRate": "48000",
            },
        )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": f"HyperFrames · {project_name}"})
    project = ET.SubElement(event, "project", {"name": project_name})

    keep = cuts if cuts else [(0.0, src_dur)]
    timeline_dur = sum(e - s for s, e in keep)

    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": _t(timeline_dur, tb),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    offset = 0.0
    words = (transcript or {}).get("words") or []
    aroll_clips: list[ET.Element] = []
    aroll_offsets: list[tuple[float, float, ET.Element]] = []  # (timeline_start, timeline_end, element)
    for i, (s, e) in enumerate(keep):
        clip_dur = max(e - s, 1.0 / 30.0)
        clip = ET.SubElement(
            spine,
            "asset-clip",
            {
                "ref": "r2",
                "name": f"{project_name} cut {i + 1}",
                "offset": _t(offset, tb),
                "start": _t(s, tb),
                "duration": _t(clip_dur, tb),
                "tcFormat": "NDF",
            },
        )
        aroll_clips.append(clip)
        aroll_offsets.append((offset, offset + clip_dur, clip))
        for w in words:
            ws = float(w.get("start") or 0.0)
            we = float(w.get("end") or ws)
            if ws >= s and ws < e:
                ET.SubElement(
                    clip,
                    "marker",
                    {
                        "start": _t(ws, tb),
                        "duration": _t(max(we - ws, 1 / 30.0), tb),
                        "value": str(w.get("word", "")).strip(),
                    },
                )
        offset += clip_dur

    # Attach B-roll placements as connected clips on lane=1, anchored to the
    # A-roll spine clip whose timeline range contains the placement start.
    for placement in broll_placements:
        ai = int(placement.get("angle_index", 0))
        if ai < 0 or ai >= len(broll_angles):
            continue
        tl_off = float(placement.get("timeline_offset", 0.0))
        tl_dur = float(placement.get("timeline_duration", 0.0))
        if tl_dur <= 0:
            continue
        ang_in = float(placement.get("angle_in", 0.0))
        # find host A-roll clip
        host = None
        host_start = 0.0
        for ts, te, elt in aroll_offsets:
            if tl_off >= ts and tl_off < te:
                host = elt
                host_start = ts
                break
        if host is None:
            continue
        ET.SubElement(
            host,
            "asset-clip",
            {
                "ref": f"b{ai + 1}",
                "lane": "1",
                "name": broll_angles[ai][0],
                "offset": _t(tl_off - host_start, tb),
                "start": _t(ang_in, tb),
                "duration": _t(tl_dur, tb),
                "audioRole": "music",
            },
        )

    return _render(fcpxml)


async def build_multicam_fcpxml(
    *,
    project_name: str,
    angles: list[tuple[str, Path]],  # [(name, path), ...]
    primary_index: int,
    cuts: list[tuple[float, float]] | None,
    transcript: dict | None = None,
) -> str:
    """Build an FCPXML with a multicam media grouping all angles.

    angles[primary_index] dictates the format / time base / cuts. All angles are
    assumed to start at offset 0 (sync responsibility belongs to the editor in FCP).
    """
    if not angles:
        raise ValueError("at least one angle is required")
    primary_name, primary_path = angles[primary_index]
    meta = await _video_meta(primary_path)
    tb = meta["time_base"]
    src_dur = meta["duration"] or 0.0

    fcpxml = ET.Element("fcpxml", {"version": "1.10"})
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": meta["format_name"],
            "frameDuration": meta["frame_duration"],
            "width": str(meta["width"]),
            "height": str(meta["height"]),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    # one asset per angle
    asset_ids: list[str] = []
    angle_durations: list[float] = []
    for i, (name, path) in enumerate(angles):
        m = await _video_meta(path)
        aid = f"a{i + 1}"
        asset_ids.append(aid)
        angle_durations.append(m["duration"] or 0.0)
        ET.SubElement(
            resources,
            "asset",
            {
                "id": aid,
                "name": name,
                "src": _file_url(path),
                "start": "0s",
                "duration": _t(m["duration"] or 0.0, tb),
                "hasVideo": "1",
                "hasAudio": "1",
                "format": "r1",
                "audioSources": "1",
                "audioChannels": "2",
                "audioRate": "48000",
            },
        )

    multi_id = "mc1"
    media = ET.SubElement(resources, "media", {"id": multi_id, "name": f"{project_name} Multicam"})
    multicam = ET.SubElement(
        media,
        "multicam",
        {
            "format": "r1",
            "tcStart": "0s",
            "tcFormat": "NDF",
            "renderColorSpace": "Rec. 709",
        },
    )
    for i, (name, _path) in enumerate(angles):
        angle = ET.SubElement(
            multicam,
            "mc-angle",
            {
                "name": name,
                "angleID": f"angle{i + 1}",
            },
        )
        ET.SubElement(
            angle,
            "asset-clip",
            {
                "ref": asset_ids[i],
                "name": name,
                "offset": "0s",
                "start": "0s",
                "duration": _t(angle_durations[i], tb),
            },
        )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": f"HyperFrames · {project_name}"})
    project = ET.SubElement(event, "project", {"name": project_name})

    keep = cuts if cuts else [(0.0, src_dur)]
    timeline_dur = sum(e - s for s, e in keep)
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": _t(timeline_dur, tb),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    primary_angle_id = f"angle{primary_index + 1}"
    offset = 0.0
    words = (transcript or {}).get("words") or []
    for i, (s, e) in enumerate(keep):
        clip_dur = max(e - s, 1.0 / 30.0)
        mc = ET.SubElement(
            spine,
            "mc-clip",
            {
                "ref": multi_id,
                "name": f"{project_name} cut {i + 1}",
                "offset": _t(offset, tb),
                "start": _t(s, tb),
                "duration": _t(clip_dur, tb),
                "tcFormat": "NDF",
            },
        )
        ET.SubElement(
            mc,
            "mc-source",
            {"angleID": primary_angle_id, "srcEnable": "all"},
        )
        for w in words:
            ws = float(w.get("start") or 0.0)
            we = float(w.get("end") or ws)
            if ws >= s and ws < e:
                ET.SubElement(
                    mc,
                    "marker",
                    {
                        "start": _t(ws, tb),
                        "duration": _t(max(we - ws, 1 / 30.0), tb),
                        "value": str(w.get("word", "")).strip(),
                    },
                )
        offset += clip_dur

    return _render(fcpxml)


# --- output helpers -----------------------------------------------------------

def _file_url(p: Path) -> str:
    abs_path = p.resolve()
    return f"file://{abs_path}"


def _render(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{body}\n'
