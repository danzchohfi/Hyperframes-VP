"""Frame-level quality detection for B-roll clips.

Two cheap metrics computed across sampled frames:

  - Blur via Laplacian variance: small variance ⇒ blurry.
  - Motion / shake via inter-frame absolute difference (a proxy for
    handheld jitter when too high).

Output is a {quality, blur_score, shake_score, frames_inspected} verdict
where `quality ∈ {"ok", "blurry", "shaky", "dark"}`.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


_BLUR_THRESHOLD = 90.0       # Laplacian variance below this = blurry
_DARK_THRESHOLD = 35.0       # mean brightness below this = too dark
_SHAKE_THRESHOLD = 20.0      # mean inter-frame diff above this = shaky


def assess(src: Path, *, max_frames: int = 24) -> dict:
    """Returns: {quality, blur_score, shake_score, brightness, frames}.

    Cheap to run (~50ms for short clips). Designed for B-roll vetting before
    placement.
    """
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return {"quality": "unknown", "blur_score": 0, "shake_score": 0, "brightness": 0, "frames": 0}

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        cap.release()
        return {"quality": "unknown", "blur_score": 0, "shake_score": 0, "brightness": 0, "frames": 0}

    step = max(1, total // max_frames)
    blurs: list[float] = []
    brights: list[float] = []
    shakes: list[float] = []
    prev_gray: np.ndarray | None = None
    inspected = 0

    for i in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # downscale for speed
        small = cv2.resize(gray, (320, max(180, gray.shape[0] * 320 // max(1, gray.shape[1]))))
        blur = float(cv2.Laplacian(small, cv2.CV_64F).var())
        bright = float(small.mean())
        blurs.append(blur)
        brights.append(bright)
        if prev_gray is not None:
            diff = cv2.absdiff(prev_gray, small)
            shakes.append(float(diff.mean()))
        prev_gray = small
        inspected += 1

    cap.release()

    blur_score = float(np.median(blurs)) if blurs else 0.0
    brightness = float(np.median(brights)) if brights else 0.0
    shake_score = float(np.median(shakes)) if shakes else 0.0

    quality = "ok"
    if brightness < _DARK_THRESHOLD:
        quality = "dark"
    elif blur_score < _BLUR_THRESHOLD:
        quality = "blurry"
    elif shake_score > _SHAKE_THRESHOLD:
        quality = "shaky"

    return {
        "quality": quality,
        "blur_score": round(blur_score, 2),
        "shake_score": round(shake_score, 2),
        "brightness": round(brightness, 2),
        "frames": inspected,
    }
