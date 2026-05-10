"""Cross-clip face fingerprinting and identity clustering.

For each clip/angle:
  1. Sample N frames.
  2. Detect faces (Haar cascade — already used by face_analysis).
  3. For each detected face, crop and compute a small embedding:
     - Resize to 96x96 grayscale.
     - Apply CLAHE (contrast normalization).
     - Flatten and L2-normalize → 9216-D feature vector.
  4. Aggregate per clip: keep up to K representative embeddings
     (the most-distinct ones via greedy farthest-point sampling).

Clustering across clips:
  - Combine all per-clip representatives.
  - Run a simple agglomerative grouping with cosine distance threshold.
  - Each cluster = one "person".

This is intentionally lightweight (no dlib/insightface). It works well for
"is this the same person across cameras of the same shoot" within reasonable
lighting consistency. It's not a face recognizer for arbitrary footage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
EMBED_SIZE = 96


def _embed_face(face_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY) if len(face_bgr.shape) == 3 else face_bgr
    gray = cv2.resize(gray, (EMBED_SIZE, EMBED_SIZE))
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    flat = gray.astype(np.float32).flatten()
    n = np.linalg.norm(flat) + 1e-9
    return flat / n


def _farthest_point_sample(embeds: list[np.ndarray], k: int = 5) -> list[int]:
    """Pick k indices from `embeds` to maximize spread."""
    if len(embeds) <= k:
        return list(range(len(embeds)))
    chosen = [0]
    while len(chosen) < k:
        best_idx = -1
        best_d = -1.0
        for i, e in enumerate(embeds):
            if i in chosen:
                continue
            d_min = min(float(np.linalg.norm(e - embeds[j])) for j in chosen)
            if d_min > best_d:
                best_d = d_min
                best_idx = i
        if best_idx < 0:
            break
        chosen.append(best_idx)
    return chosen


def _detect_face_crops(src: Path, *, max_frames: int = 24) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return []
    cascade = cv2.CascadeClassifier(_CASCADE_PATH)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        cap.release()
        return []
    step = max(1, total // max_frames)
    crops: list[np.ndarray] = []
    for i in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_eq = cv2.equalizeHist(gray)
        rects = cascade.detectMultiScale(gray_eq, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(60, 60))
        for (x, y, w, h) in rects:
            # crop with small padding
            pad = int(h * 0.15)
            x0 = max(0, x - pad); y0 = max(0, y - pad)
            x1 = min(frame.shape[1], x + w + pad); y1 = min(frame.shape[0], y + h + pad)
            crops.append(frame[y0:y1, x0:x1].copy())
    cap.release()
    return crops


def fingerprint_clip(src: Path, *, k_representatives: int = 5, max_frames: int = 24) -> list[list[float]]:
    """Returns up to `k_representatives` 96*96-D L2-normalized vectors as lists."""
    crops = _detect_face_crops(src, max_frames=max_frames)
    if not crops:
        return []
    embeds = [_embed_face(c) for c in crops]
    keep_idx = _farthest_point_sample(embeds, k=k_representatives)
    return [embeds[i].tolist() for i in keep_idx]


def crop_for_cluster(
    src_path: Path,
    *,
    min_face_size: int = 100,
    max_frames: int = 24,
) -> bytes | None:
    """Find the best face crop in `src_path` (largest, most-centered) and
    return it as JPEG bytes. Returns None if no face is found."""
    cap = cv2.VideoCapture(str(src_path))
    if not cap.isOpened():
        return None
    cascade = cv2.CascadeClassifier(_CASCADE_PATH)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        cap.release()
        return None
    step = max(1, total // max_frames)
    best = None
    best_score = 0.0
    for i in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        rects = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(min_face_size, min_face_size))
        for (x, y, w, h) in rects:
            cx = (x + w / 2) / max(1, frame.shape[1])
            cy = (y + h / 2) / max(1, frame.shape[0])
            # Centered + large = better
            centering = 1.0 - (abs(cx - 0.5) + abs(cy - 0.5))
            score = (w * h) * (0.5 + 0.5 * centering)
            if score > best_score:
                best_score = score
                pad_x = int(w * 0.4)
                pad_y = int(h * 0.4)
                x0 = max(0, x - pad_x); y0 = max(0, y - pad_y)
                x1 = min(frame.shape[1], x + w + pad_x); y1 = min(frame.shape[0], y + h + pad_y)
                best = frame[y0:y1, x0:x1].copy()
    cap.release()
    if best is None:
        return None
    # Resize to 256
    h, w = best.shape[:2]
    if max(h, w) > 256:
        scale = 256 / max(h, w)
        best = cv2.resize(best, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".jpg", best, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return bytes(buf) if ok else None


def cluster_identities(
    fingerprints_per_source: dict[str, list[list[float]]],
    *,
    cosine_distance_threshold: float = 0.45,
) -> dict[str, Any]:
    """Group representatives across sources into identity clusters.

    Returns:
      {
        "clusters": [{
          "id": "person_1",
          "size": <count of contributing embeddings>,
          "sources": ["source", "angle1", "clip_xyz", ...],
        }, ...],
        "presence": {  # which clusters are present in each source
          "<source_id>": ["person_1", "person_2"]
        }
      }
    """
    flat: list[tuple[str, np.ndarray]] = []
    for src_id, embeds in fingerprints_per_source.items():
        for e in embeds:
            flat.append((src_id, np.array(e, dtype=np.float32)))
    if not flat:
        return {"clusters": [], "presence": {sid: [] for sid in fingerprints_per_source}}

    # Greedy single-link clustering
    cluster_centroids: list[np.ndarray] = []
    cluster_members: list[list[tuple[str, np.ndarray]]] = []
    for src_id, vec in flat:
        # find closest cluster
        best_cid = -1
        best_d = 1.0
        for cid, c in enumerate(cluster_centroids):
            d = float(1.0 - np.dot(vec, c))  # cosine distance (vectors are L2-normed)
            if d < best_d:
                best_d = d
                best_cid = cid
        if best_cid >= 0 and best_d <= cosine_distance_threshold:
            cluster_members[best_cid].append((src_id, vec))
            # Recompute centroid (mean then re-normalize)
            mean = np.mean([m[1] for m in cluster_members[best_cid]], axis=0)
            mean /= np.linalg.norm(mean) + 1e-9
            cluster_centroids[best_cid] = mean
        else:
            cluster_centroids.append(vec.copy())
            cluster_members.append([(src_id, vec)])

    # Sort clusters by member count descending
    order = sorted(range(len(cluster_members)), key=lambda i: -len(cluster_members[i]))
    clusters: list[dict[str, Any]] = []
    presence: dict[str, list[str]] = {sid: [] for sid in fingerprints_per_source}
    for rank, cid in enumerate(order):
        members = cluster_members[cid]
        cluster_id = f"person_{rank + 1}"
        sources_seen = sorted({m[0] for m in members})
        clusters.append({
            "id": cluster_id,
            "size": len(members),
            "sources": sources_seen,
        })
        for sid in sources_seen:
            if cluster_id not in presence[sid]:
                presence[sid].append(cluster_id)

    return {"clusters": clusters, "presence": presence}
