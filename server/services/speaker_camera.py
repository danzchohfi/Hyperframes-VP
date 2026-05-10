"""Map diarized speakers (Speaker A, Speaker B...) to face clusters
(person_1, person_2...) and pick the best camera per turn so the active
speaker is visible.

Method:
  - For each (speaker_turn, source) pair, sample the source within the turn's
    time window and detect faces.
  - For each face, look up which face cluster it belongs to (via the cached
    fingerprints — we re-embed and find the nearest centroid in face
    identities).
  - Tally: speaker → cluster → count of frames visible.
  - The dominant cluster per speaker = "this is the person speaking".

Then a smarter camera picker: for each turn, prefer the source where the
speaker's mapped cluster is CURRENTLY visible (close-up if available).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import face_identity as face_id_svc


def _nearest_cluster(embed: np.ndarray, centroids: list[tuple[str, np.ndarray]],
                     *, threshold: float = 0.55) -> str | None:
    best_id = None
    best_d = 1.0
    for cid, c in centroids:
        d = float(1.0 - np.dot(embed, c))
        if d < best_d:
            best_d = d
            best_id = cid
    return best_id if best_d <= threshold else None


def _faces_in_window(src: Path, *, t0: float, t1: float, max_samples: int = 4) -> list[np.ndarray]:
    """Sample up to N frames in [t0, t1], return embeddings of detected faces."""
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return []
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    out: list[np.ndarray] = []
    samples = min(max_samples, max(1, int((t1 - t0) * 2)))
    if samples <= 0:
        cap.release()
        return out
    for k in range(samples):
        t = t0 + (t1 - t0) * (k + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        cascade = cv2.CascadeClassifier(face_id_svc._CASCADE_PATH)
        rects = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(60, 60))
        for (x, y, w, h) in rects:
            crop = frame[max(0, y):y + h, max(0, x):x + w]
            if crop.size == 0:
                continue
            out.append(face_id_svc._embed_face(crop))
    cap.release()
    return out


def map_speakers_to_clusters(
    *,
    turns: list[dict[str, Any]],
    sources: list[tuple[str, Path]],
    cluster_centroids: list[tuple[str, np.ndarray]],
) -> dict[str, str | None]:
    """Returns {speaker_label: cluster_id_or_None}."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for turn in turns:
        sp = turn.get("speaker") or "?"
        s = float(turn.get("start") or 0.0)
        e = float(turn.get("end") or s)
        if e - s <= 0.4:
            continue
        for _name, src in sources:
            if not src.exists():
                continue
            embeds = _faces_in_window(src, t0=s, t1=e)
            for emb in embeds:
                cid = _nearest_cluster(emb, cluster_centroids)
                if cid:
                    counts[sp][cid] += 1
    mapping: dict[str, str | None] = {}
    for sp, ctr in counts.items():
        if ctr:
            mapping[sp] = ctr.most_common(1)[0][0]
        else:
            mapping[sp] = None
    return mapping
