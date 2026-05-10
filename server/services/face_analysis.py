"""Per-clip face/subject analysis for multicam decisions.

For each angle we sample N frames and run OpenCV's Haar cascade face
detector. We derive:

  - face_presence: fraction of sampled frames that contain a face
  - face_area_avg: median face area as fraction of frame area
  - face_x_avg: median horizontal center of the dominant face (0..1)
  - shot_type: close_up | medium | wide | no_face (heuristic on face area)
  - face_count_avg: median number of faces detected per frame
  - subject_change: True if the FIRST and LAST third of the clip have
                    materially different face signatures (different person /
                    framing / nobody-in-frame switch)

This lets the camera-selection step pick the camera that's currently
showing the speaker / pick the wide shot when nobody is on-camera.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_PROFILE_PATH = cv2.data.haarcascades + "haarcascade_profileface.xml"


def _detect_faces(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    cascade = cv2.CascadeClassifier(_CASCADE_PATH)
    rects = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                     minSize=(30, 30))
    # also profile faces
    try:
        profile = cv2.CascadeClassifier(_PROFILE_PATH)
        prof = profile.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                        minSize=(30, 30))
        if len(prof):
            rects = list(rects) + list(prof) if len(rects) else list(prof)
    except Exception:
        pass
    return [tuple(map(int, r)) for r in rects]


def _shot_type(face_area_frac: float, face_count: int) -> str:
    if face_count == 0:
        return "no_face"
    if face_area_frac >= 0.10:
        return "close_up"
    if face_area_frac >= 0.025:
        return "medium"
    return "wide"


def _face_fingerprint(rects: list[tuple[int, int, int, int]], width: int, height: int) -> dict:
    if not rects:
        return {"count": 0, "area_frac": 0.0, "x": 0.5}
    # dominant face = largest
    rects_sorted = sorted(rects, key=lambda r: -r[2] * r[3])
    x, y, w, h = rects_sorted[0]
    return {
        "count": len(rects),
        "area_frac": (w * h) / max(1, width * height),
        "x": (x + w / 2) / max(1, width),
    }


def analyze(src: Path, *, max_frames: int = 30) -> dict:
    """Returns a full per-clip face analysis."""
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return {
            "face_presence": 0.0, "face_area_avg": 0.0, "face_x_avg": 0.5,
            "face_count_avg": 0, "shot_type": "no_face",
            "subject_change": False, "frames": 0,
        }
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    if total <= 0:
        cap.release()
        return {
            "face_presence": 0.0, "face_area_avg": 0.0, "face_x_avg": 0.5,
            "face_count_avg": 0, "shot_type": "no_face",
            "subject_change": False, "frames": 0,
        }
    step = max(1, total // max_frames)
    fingerprints: list[dict] = []
    inspected = 0
    for i in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        # downscale large frames for speed
        scale = max(1.0, gray.shape[1] / 720)
        if scale > 1.0:
            small = cv2.resize(gray, (int(gray.shape[1] / scale), int(gray.shape[0] / scale)))
        else:
            small = gray
        rects = _detect_faces(small)
        # rescale rects back
        if scale > 1.0:
            rects = [(int(x * scale), int(y * scale), int(w * scale), int(h * scale)) for (x, y, w, h) in rects]
        fp = _face_fingerprint(rects, width, height)
        fp["frame_idx"] = i
        fingerprints.append(fp)
        inspected += 1
    cap.release()

    if not fingerprints:
        return {
            "face_presence": 0.0, "face_area_avg": 0.0, "face_x_avg": 0.5,
            "face_count_avg": 0, "shot_type": "no_face",
            "subject_change": False, "frames": inspected,
        }

    with_face = [f for f in fingerprints if f["count"] > 0]
    presence = len(with_face) / len(fingerprints)
    if with_face:
        area_avg = float(np.median([f["area_frac"] for f in with_face]))
        x_avg = float(np.median([f["x"] for f in with_face]))
        count_avg = float(np.median([f["count"] for f in with_face]))
    else:
        area_avg = 0.0
        x_avg = 0.5
        count_avg = 0.0

    shot = _shot_type(area_avg, int(round(count_avg)))

    # Subject change: split into thirds (first / mid / last), compare signatures.
    third = max(1, len(fingerprints) // 3)
    first = fingerprints[:third]
    last = fingerprints[-third:]

    def _agg(group: list[dict]) -> dict:
        gf = [g for g in group if g["count"] > 0]
        if not gf:
            return {"presence": 0.0, "x": 0.5, "area": 0.0}
        return {
            "presence": len(gf) / len(group),
            "x": float(np.median([g["x"] for g in gf])),
            "area": float(np.median([g["area_frac"] for g in gf])),
        }

    fa = _agg(first)
    la = _agg(last)
    presence_delta = abs(fa["presence"] - la["presence"])
    x_delta = abs(fa["x"] - la["x"])
    area_delta = abs(fa["area"] - la["area"])
    subject_change = bool(
        presence_delta > 0.45  # nobody → somebody (or vice-versa)
        or x_delta > 0.25      # face moved across the frame
        or area_delta > 0.06   # zoom/scale changed significantly
    )

    return {
        "face_presence": round(presence, 3),
        "face_area_avg": round(area_avg, 4),
        "face_x_avg": round(x_avg, 3),
        "face_count_avg": round(count_avg, 2),
        "shot_type": shot,
        "subject_change": subject_change,
        "first_segment": fa,
        "last_segment": la,
        "frames": inspected,
    }
