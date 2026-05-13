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


# --- color profile helpers ---------------------------------------------------

# Maps our internal profile keys to user-facing labels + the Camera LUT name
# Final Cut expects in its Inspector picker. The note is dropped as a hint on
# every asset; FCPXML 1.10 doesn't have a standard attribute for non-Rec.709
# log profiles, so the user assigns the LUT once in FCP and ripples it.
LOG_PROFILES: dict[str, dict[str, str]] = {
    "rec709":   {"label": "Rec. 709", "fcp_lut": ""},
    "slog3":    {"label": "Sony S-Log3 / S-Gamut3.Cine", "fcp_lut": "Sony S-Log3 S-Gamut3.Cine to LC-709"},
    "clog3":    {"label": "Canon C-Log3 / Cinema Gamut",  "fcp_lut": "Canon Log3 Cinema Gamut to Rec.709 BT.1886"},
    "vlog":     {"label": "Panasonic V-Log / V-Gamut",    "fcp_lut": "Panasonic V-Log V-Gamut to BT.709"},
    "applelog": {"label": "Apple Log",                    "fcp_lut": "Apple Log to Rec. 709"},
    "logc":     {"label": "ARRI Log-C",                   "fcp_lut": "Arri LogC to Rec. 709"},
    "custom":   {"label": "Custom LUT (uploaded)", "fcp_lut": ""},
}


def _emit_asset(
    resources: ET.Element,
    *,
    asset_id: str,
    name: str,
    src_path: Path,
    duration_str: str,
    format_id: str = "r1",
    has_video: bool = True,
    has_audio: bool = True,
    audio_channels: int = 2,
    audio_rate: int = 48000,
    color_profile: str | None = None,
) -> ET.Element:
    """Emit a DTD-compliant <asset> for FCPXML 1.10.

    Schema (1.10): <asset> body is `(media-rep+, metadata?)`. The actual
    file path lives on a <media-rep kind="original-media" src="..."/>
    child — NOT as a `src` attribute on `<asset>` (which DTD validation
    in Final Cut rejects). Color/profile hints go into the optional
    <metadata> block via `<md key value/>` entries.
    """
    attrs: dict[str, str] = {
        "id": asset_id,
        "name": name,
        "start": "0s",
        "duration": duration_str,
        "hasVideo": "1" if has_video else "0",
        "hasAudio": "1" if has_audio else "0",
        "format": format_id,
    }
    if has_audio:
        attrs["audioSources"] = "1"
        attrs["audioChannels"] = str(audio_channels)
        attrs["audioRate"] = str(audio_rate)
    asset_el = ET.SubElement(resources, "asset", attrs)
    ET.SubElement(asset_el, "media-rep", {
        "kind": "original-media",
        "src": _file_url(src_path),
    })
    # Optional metadata block: log profile hint for the editor + any
    # internal traceability keys. Must follow <media-rep> per DTD.
    if color_profile and color_profile != "rec709":
        spec = LOG_PROFILES.get(color_profile, {})
        label = spec.get("label", color_profile)
        lut = spec.get("fcp_lut") or ""
        md = ET.SubElement(asset_el, "metadata")
        ET.SubElement(md, "md", {"key": "com.hyperframes.colorProfile", "value": color_profile})
        ET.SubElement(md, "md", {"key": "com.hyperframes.colorProfileLabel", "value": label})
        if lut:
            ET.SubElement(md, "md", {"key": "com.hyperframes.cameraLUT", "value": lut})
    return asset_el


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


def _t(seconds: float, tb: int, fd_num: int = 1) -> str:
    """Frame-aligned rational time.

    FCPXML requires every offset/duration on a clip/sequence to land on
    an edit-frame boundary. For NTSC rates (29.97 / 23.976 / 59.94)
    the frame duration is e.g. 1001/30000s, so every value must be
    `n * 1001 / 30000` for some integer n. Otherwise FCP rejects the
    XML with "The item is not on an edit frame boundary".

    Pass `fd_num` from the format's `frame_duration` string (the
    numerator, e.g. 1001 for 29.97). When omitted, falls back to
    tick-level rounding — fine for 24/25/30/50/60 since their frame
    duration numerator is 100 and the time base is a multiple of 100,
    but unsafe for NTSC rates. Use the metadata.
    """
    if seconds <= 0:
        return "0s"
    frames = round(seconds * tb / max(fd_num, 1))
    ticks = frames * max(fd_num, 1)
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
    # Parse numerator out of e.g. "1001/30000s" so callers can pass it
    # to _t() for proper NTSC frame snapping.
    try:
        fd_num = int(fd.split("/", 1)[0])
    except (ValueError, IndexError):
        fd_num = 1
    duration = float(info.get("format", {}).get("duration") or 0.0)
    return {
        "width": width,
        "height": height,
        "frame_duration": fd,
        "frame_num": fd_num,
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
    source_color_profile: str = "rec709",
) -> str:
    meta = await _video_meta(source)
    tb = meta["time_base"]
    fd_num = meta["frame_num"]
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
    _emit_asset(
        resources,
        asset_id="r2",
        name=source.stem,
        src_path=source,
        duration_str=_t(src_dur, tb, fd_num),
        color_profile=source_color_profile,
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
            "duration": _t(timeline_dur, tb, fd_num),
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
                "offset": _t(offset, tb, fd_num),
                "start": _t(s, tb, fd_num),
                "duration": _t(clip_dur, tb, fd_num),
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
                        "start": _t(ws, tb, fd_num),
                        "duration": _t(max(we - ws, 1 / 30.0), tb, fd_num),
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
    source_color_profile: str = "rec709",
) -> str:
    """A-roll spine on V1, B-roll inserts as connected clips on lane=1.

    `broll_placements` items: {angle_index, angle_in, angle_out,
    timeline_offset, timeline_duration, soundbite_id?}.
    """
    meta = await _video_meta(source)
    tb = meta["time_base"]
    fd_num = meta["frame_num"]
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
    _emit_asset(
        resources,
        asset_id="r2",
        name=source.stem,
        src_path=source,
        duration_str=_t(src_dur, tb, fd_num),
        color_profile=source_color_profile,
    )
    # B-roll assets
    angle_durations: dict[int, float] = {}
    for i, (name, path) in enumerate(broll_angles):
        m = await _video_meta(path)
        angle_durations[i] = m["duration"] or 0.0
        _emit_asset(
            resources,
            asset_id=f"b{i + 1}",
            name=name,
            src_path=path,
            duration_str=_t(m["duration"] or 0.0, tb, fd_num),
            color_profile=source_color_profile,
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
            "duration": _t(timeline_dur, tb, fd_num),
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
                "offset": _t(offset, tb, fd_num),
                "start": _t(s, tb, fd_num),
                "duration": _t(clip_dur, tb, fd_num),
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
                        "start": _t(ws, tb, fd_num),
                        "duration": _t(max(we - ws, 1 / 30.0), tb, fd_num),
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
                "offset": _t(tl_off - host_start, tb, fd_num),
                "start": _t(ang_in, tb, fd_num),
                "duration": _t(tl_dur, tb, fd_num),
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
    angle_offsets: list[float] | None = None,  # seconds; positive = clip starts later
    camera_cuts: list[dict] | None = None,     # [{start, end, angle_index, reason?}, ...]
    chapters: list[dict] | None = None,        # [{start, title}]
    soundbites: list[dict] | None = None,      # [{start, end, topic?, quote?}]
    speakers: list[dict] | None = None,        # [{start, end, speaker}]
    source_color_profile: str = "rec709",
) -> str:
    """Build an FCPXML with a multicam media grouping all angles.

    angles[primary_index] dictates the format / time base / cuts.
    `angle_offsets` (optional) shifts each angle inside the multicam by the
    given seconds — supply from `audio_sync.compute_offsets`. The reference
    angle should have offset 0.

    If `camera_cuts` is provided, one `<mc-clip>` is emitted per cut and the
    `<mc-source angleID="...">` is set to the cut's `angle_index` — i.e. the
    automatic camera-switch decisions land in the timeline so the editor only
    fine-tunes them. When `cuts` (silence/roughcut trim) is also provided,
    `camera_cuts` are intersected with `cuts`.

    Chapter/soundbite/speaker lists become timeline markers placed at their
    `start` times.
    """
    if not angles:
        raise ValueError("at least one angle is required")
    primary_name, primary_path = angles[primary_index]
    meta = await _video_meta(primary_path)
    tb = meta["time_base"]
    fd_num = meta["frame_num"]
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
        _emit_asset(
            resources,
            asset_id=aid,
            name=name,
            src_path=path,
            duration_str=_t(m["duration"] or 0.0, tb, fd_num),
            color_profile=source_color_profile,
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
        # If we have an offset, place the asset-clip at -offset inside the
        # multicam timeline so the audio sync ties up.
        off = 0.0
        if angle_offsets and i < len(angle_offsets):
            off = max(0.0, float(angle_offsets[i]))
        ET.SubElement(
            angle,
            "asset-clip",
            {
                "ref": asset_ids[i],
                "name": name,
                "offset": _t(off, tb, fd_num),
                "start": "0s",
                "duration": _t(angle_durations[i], tb, fd_num),
            },
        )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": f"HyperFrames · {project_name}"})
    project = ET.SubElement(event, "project", {"name": project_name})

    keep = cuts if cuts else [(0.0, src_dur)]
    primary_angle_id = f"angle{primary_index + 1}"

    # Build the list of (start, end, angle_index) segments to place on the spine.
    # When camera_cuts is provided, intersect them with `keep` so silence cuts
    # are still honored. Otherwise emit one segment per `keep` range, all on
    # the primary angle (legacy behavior).
    segments: list[tuple[float, float, int]] = []
    if camera_cuts:
        for s, e in keep:
            for cc in camera_cuts:
                cs = float(cc.get("start") or 0.0)
                ce = float(cc.get("end") or 0.0)
                ai = int(cc.get("angle_index") or 0)
                a = max(s, cs)
                b = min(e, ce)
                if b - a > 1.0 / 60.0:
                    segments.append((a, b, ai))
        segments.sort(key=lambda r: r[0])
    if not segments:
        for s, e in keep:
            segments.append((s, e, primary_index))

    timeline_dur = sum(e - s for s, e, _ in segments)
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": _t(timeline_dur, tb, fd_num),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    # Markers (chapters / soundbites / speakers / words) are anchored to the
    # mc-clip whose [s,e) contains the marker's start. Build a quick index.
    words = (transcript or {}).get("words") or []

    def _truncate(text: str, n: int = 60) -> str:
        text = (text or "").strip().replace("\n", " ")
        return text if len(text) <= n else text[: n - 1] + "…"

    def _marker_for(start_s: float, value: str, dur_s: float = 1 / 30.0) -> dict:
        return {"start": _t(start_s, tb, fd_num), "duration": _t(max(dur_s, 1 / 30.0), tb, fd_num), "value": value}

    # Pre-collect all markers into (start, marker_dict) so we can place them
    # on the correct clip. Each marker is rendered relative to the source
    # timeline (start = original seconds), which FCPX requires for mc-clip
    # markers — they reference the underlying media time.
    extra_markers: list[tuple[float, dict]] = []
    if chapters:
        for ch in chapters:
            try:
                t = float(ch.get("start") or ch.get("time") or 0.0)
            except (TypeError, ValueError):
                continue
            title = _truncate(str(ch.get("title") or ch.get("name") or "Chapter"))
            extra_markers.append((t, _marker_for(t, f"Ch · {title}")))
    if soundbites:
        for sb in soundbites:
            try:
                t = float(sb.get("start") or 0.0)
                dur = max(float(sb.get("end") or t) - t, 1 / 30.0)
            except (TypeError, ValueError):
                continue
            topic = _truncate(str(sb.get("topic") or sb.get("title") or "Bite"), 24)
            quote = _truncate(str(sb.get("quote") or sb.get("text") or ""), 40)
            label = f"🎯 {topic}" + (f": {quote}" if quote else "")
            extra_markers.append((t, _marker_for(t, label, dur)))
    if speakers:
        last_speaker = None
        for sp in speakers:
            try:
                t = float(sp.get("start") or 0.0)
            except (TypeError, ValueError):
                continue
            name = _truncate(str(sp.get("speaker") or sp.get("name") or "Speaker"), 24)
            if name == last_speaker:
                continue
            last_speaker = name
            extra_markers.append((t, _marker_for(t, f"🎙 {name}")))

    offset = 0.0
    for i, (s, e, ai) in enumerate(segments):
        clip_dur = max(e - s, 1.0 / 30.0)
        angle_id = f"angle{max(0, min(ai, len(angles) - 1)) + 1}"
        mc = ET.SubElement(
            spine,
            "mc-clip",
            {
                "ref": multi_id,
                "name": f"{project_name} · {angles[max(0, min(ai, len(angles) - 1))][0]}",
                "offset": _t(offset, tb, fd_num),
                "start": _t(s, tb, fd_num),
                "duration": _t(clip_dur, tb, fd_num),
            },
        )
        ET.SubElement(
            mc,
            "mc-source",
            {"angleID": angle_id, "srcEnable": "all"},
        )
        # Word markers from transcript (only when explicitly enabled upstream).
        for w in words:
            ws = float(w.get("start") or 0.0)
            we = float(w.get("end") or ws)
            if ws >= s and ws < e:
                ET.SubElement(
                    mc,
                    "marker",
                    {
                        "start": _t(ws, tb, fd_num),
                        "duration": _t(max(we - ws, 1 / 30.0), tb, fd_num),
                        "value": str(w.get("word", "")).strip(),
                    },
                )
        # Chapter/soundbite/speaker markers.
        for mt, mattrs in extra_markers:
            if mt >= s and mt < e:
                ET.SubElement(mc, "marker", mattrs)
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
