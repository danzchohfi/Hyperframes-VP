"""Build a fine-grained subject-presence timeline within a single clip.

Samples the clip every `step_seconds` and reports who's visible. Used by the
mid-clip camera switcher so we can cut to a different camera the moment a
new person enters.

Output:
  {
    "step_seconds": 0.5,
    "duration": 30.0,
    "events": [
      { "t": 0.0,  "face_count": 1, "face_x": 0.5, "shot_type": "close_up" },
      { "t": 0.5,  "face_count": 2, ...},   # 2nd person entered
      ...
    ]
  }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def _shot_type(face_area_frac: float, face_count: int) -> str:
    if face_count == 0:
        return "no_face"
    if face_area_frac >= 0.10:
        return "close_up"
    if face_area_frac >= 0.025:
        return "medium"
    return "wide"


def build(src: Path, *, step_seconds: float = 0.5) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return {"step_seconds": step_seconds, "duration": 0.0, "events": []}
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        cap.release()
        return {"step_seconds": step_seconds, "duration": 0.0, "events": []}
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or 1
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or 1
    duration = total / fps if fps > 0 else 0.0

    cascade = cv2.CascadeClassifier(_CASCADE_PATH)
    step_frames = max(1, int(round(step_seconds * fps)))
    events: list[dict[str, Any]] = []
    for i in range(0, total, step_frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        # downscale for speed
        scale = max(1.0, gray.shape[1] / 720)
        if scale > 1.0:
            small = cv2.resize(gray, (int(gray.shape[1] / scale), int(gray.shape[0] / scale)))
        else:
            small = gray
        rects = cascade.detectMultiScale(small, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(40, 40))
        face_count = len(rects)
        if face_count > 0:
            # use the largest face for area / x
            largest = max(rects, key=lambda r: r[2] * r[3])
            x, y, w, h = largest
            face_area = (w * h) / max(1, small.shape[0] * small.shape[1])
            face_x = (x + w / 2) / max(1, small.shape[1])
        else:
            face_area = 0.0
            face_x = 0.5
        events.append({
            "t": round(i / fps, 3),
            "face_count": face_count,
            "face_x": round(float(face_x), 3),
            "face_area": round(float(face_area), 4),
            "shot_type": _shot_type(face_area, face_count),
        })
    cap.release()
    return {"step_seconds": step_seconds, "duration": duration, "events": events}


def detect_changes(events: list[dict[str, Any]], *,
                   x_delta: float = 0.18,
                   area_delta: float = 0.04) -> list[dict[str, Any]]:
    """Walk events and emit change-points where the dominant face moved
    significantly, count changed, or somebody entered/left frame."""
    if not events:
        return []
    changes: list[dict[str, Any]] = []
    prev = events[0]
    for ev in events[1:]:
        cnt_changed = ev["face_count"] != prev["face_count"]
        x_changed = abs(ev["face_x"] - prev["face_x"]) >= x_delta
        a_changed = abs(ev["face_area"] - prev["face_area"]) >= area_delta
        if cnt_changed or x_changed or a_changed:
            reason = []
            if cnt_changed:
                reason.append(f"count {prev['face_count']}→{ev['face_count']}")
            if x_changed:
                reason.append(f"x {prev['face_x']}→{ev['face_x']}")
            if a_changed:
                reason.append(f"area {prev['face_area']}→{ev['face_area']}")
            changes.append({"t": ev["t"], "reason": " · ".join(reason)})
        prev = ev
    return changes
