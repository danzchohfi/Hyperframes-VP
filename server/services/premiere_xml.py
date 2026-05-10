"""Final Cut Pro 7 XML (xmeml) export.

This is the format Premiere Pro and DaVinci Resolve both import natively.
We emit a simple V1+A1+A2 timeline with one clipitem per kept segment.
Time units are integer frames at the source frame rate.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .ffmpeg import probe


async def _meta(src: Path) -> dict[str, Any]:
    info = await probe(src)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    width = int(v.get("width") or 1920)
    height = int(v.get("height") or 1080)
    rf = (v.get("r_frame_rate") or "30/1").split("/")
    num = int(rf[0])
    den = int(rf[1]) if len(rf) > 1 and int(rf[1]) > 0 else 1
    rate = num / den
    timebase = round(rate)
    ntsc = abs(rate - timebase) > 0.01  # 23.976, 29.97, 59.94 → ntsc=TRUE
    return {
        "width": width,
        "height": height,
        "fps": rate,
        "timebase": timebase,
        "ntsc": ntsc,
        "duration": float(info.get("format", {}).get("duration") or 0.0),
    }


def _frames(seconds: float, fps: float) -> int:
    return max(0, round(seconds * fps))


def _rate_xml(parent: ET.Element, timebase: int, ntsc: bool) -> None:
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = str(timebase)
    ET.SubElement(r, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _file_url(p: Path) -> str:
    return f"file://{p.resolve()}"


async def build_xmeml(
    *,
    project_name: str,
    source: Path,
    cuts: list[tuple[float, float]] | None,
    broll_angles: list[tuple[str, Path]] | None = None,
    broll_placements: list[dict] | None = None,
) -> str:
    meta = await _meta(source)
    fps = meta["fps"]
    tb = meta["timebase"]
    ntsc = meta["ntsc"]

    keep = cuts if cuts else [(0.0, meta["duration"] or 0.0)]
    seq_dur = _frames(sum(e - s for s, e in keep), fps)
    broll_angles = broll_angles or []
    broll_placements = broll_placements or []

    xmeml = ET.Element("xmeml", {"version": "5"})
    project = ET.SubElement(xmeml, "project")
    ET.SubElement(project, "name").text = project_name
    children = ET.SubElement(project, "children")

    seq = ET.SubElement(children, "sequence", {"id": "seq-1"})
    ET.SubElement(seq, "name").text = project_name
    ET.SubElement(seq, "duration").text = str(seq_dur)
    _rate_xml(seq, tb, ntsc)

    media = ET.SubElement(seq, "media")

    # Single video track
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    _rate_xml(sc, tb, ntsc)
    ET.SubElement(sc, "width").text = str(meta["width"])
    ET.SubElement(sc, "height").text = str(meta["height"])
    vtrack = ET.SubElement(video, "track")
    vtrack_b = ET.SubElement(video, "track") if broll_placements else None

    # Two audio tracks (stereo split commonly expected by Premiere)
    audio = ET.SubElement(media, "audio")
    a_fmt = ET.SubElement(audio, "format")
    a_sc = ET.SubElement(a_fmt, "samplecharacteristics")
    ET.SubElement(a_sc, "depth").text = "16"
    ET.SubElement(a_sc, "samplerate").text = "48000"
    a_track1 = ET.SubElement(audio, "track")
    a_track2 = ET.SubElement(audio, "track")

    file_id = "file-1"
    src_dur_frames = _frames(meta["duration"], fps)

    timeline_pos = 0
    for i, (s, e) in enumerate(keep):
        in_f = _frames(s, fps)
        out_f = _frames(e, fps)
        clip_dur = out_f - in_f
        if clip_dur <= 0:
            continue
        cstart = timeline_pos
        cend = timeline_pos + clip_dur

        # Video clipitem
        ci_v = ET.SubElement(vtrack, "clipitem", {"id": f"v-clip-{i+1}"})
        ET.SubElement(ci_v, "name").text = f"{project_name} cut {i+1}"
        ET.SubElement(ci_v, "duration").text = str(src_dur_frames)
        _rate_xml(ci_v, tb, ntsc)
        ET.SubElement(ci_v, "in").text = str(in_f)
        ET.SubElement(ci_v, "out").text = str(out_f)
        ET.SubElement(ci_v, "start").text = str(cstart)
        ET.SubElement(ci_v, "end").text = str(cend)

        if i == 0:
            f = ET.SubElement(ci_v, "file", {"id": file_id})
            ET.SubElement(f, "name").text = source.name
            ET.SubElement(f, "pathurl").text = _file_url(source)
            _rate_xml(f, tb, ntsc)
            ET.SubElement(f, "duration").text = str(src_dur_frames)
            f_media = ET.SubElement(f, "media")
            f_video = ET.SubElement(f_media, "video")
            f_v_sc = ET.SubElement(f_video, "samplecharacteristics")
            _rate_xml(f_v_sc, tb, ntsc)
            ET.SubElement(f_v_sc, "width").text = str(meta["width"])
            ET.SubElement(f_v_sc, "height").text = str(meta["height"])
            f_audio = ET.SubElement(f_media, "audio")
            ET.SubElement(f_audio, "channelcount").text = "2"
        else:
            ET.SubElement(ci_v, "file", {"id": file_id})

        # Audio clipitems referencing same file
        for ch_idx, atrack in enumerate((a_track1, a_track2), start=1):
            ci_a = ET.SubElement(atrack, "clipitem", {"id": f"a{ch_idx}-clip-{i+1}"})
            ET.SubElement(ci_a, "name").text = f"{project_name} cut {i+1}"
            ET.SubElement(ci_a, "duration").text = str(src_dur_frames)
            _rate_xml(ci_a, tb, ntsc)
            ET.SubElement(ci_a, "in").text = str(in_f)
            ET.SubElement(ci_a, "out").text = str(out_f)
            ET.SubElement(ci_a, "start").text = str(cstart)
            ET.SubElement(ci_a, "end").text = str(cend)
            ET.SubElement(ci_a, "file", {"id": file_id})
            sc_a = ET.SubElement(ci_a, "sourcetrack")
            ET.SubElement(sc_a, "mediatype").text = "audio"
            ET.SubElement(sc_a, "trackindex").text = str(ch_idx)

        timeline_pos = cend

    # B-roll inserts on second video track
    if vtrack_b is not None and broll_placements:
        emitted_files: set[int] = set()
        for j, p in enumerate(broll_placements):
            ai = int(p.get("angle_index", -1))
            if ai < 0 or ai >= len(broll_angles):
                continue
            angle_name, angle_path = broll_angles[ai]
            tl_off = _frames(float(p.get("timeline_offset", 0.0)), fps)
            tl_dur = _frames(float(p.get("timeline_duration", 0.0)), fps)
            if tl_dur <= 0:
                continue
            a_in = _frames(float(p.get("angle_in", 0.0)), fps)
            a_out = a_in + tl_dur

            ci = ET.SubElement(vtrack_b, "clipitem", {"id": f"b-clip-{j+1}"})
            ET.SubElement(ci, "name").text = angle_name
            ET.SubElement(ci, "duration").text = str(tl_dur)
            _rate_xml(ci, tb, ntsc)
            ET.SubElement(ci, "in").text = str(a_in)
            ET.SubElement(ci, "out").text = str(a_out)
            ET.SubElement(ci, "start").text = str(tl_off)
            ET.SubElement(ci, "end").text = str(tl_off + tl_dur)
            file_id = f"b-file-{ai + 1}"
            if ai not in emitted_files:
                f = ET.SubElement(ci, "file", {"id": file_id})
                ET.SubElement(f, "name").text = angle_path.name
                ET.SubElement(f, "pathurl").text = _file_url(angle_path)
                _rate_xml(f, tb, ntsc)
                emitted_files.add(ai)
            else:
                ET.SubElement(ci, "file", {"id": file_id})

    ET.indent(xmeml, space="  ")
    body = ET.tostring(xmeml, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n{body}\n'
