"""Detect groups of clips that are likely re-takes of the same line.

Useful when the user uploads "10 takes of the same intro" — we group them so
the LLM can pick the best one.

Method: word-shingled Jaccard similarity over the first 30 normalized tokens
of each transcript. Any pair of clips with similarity above the threshold
joins the same group via union-find.

Once grouped, score each clip in the group on:
  - audio loudness (closer to -18 dBFS = better)
  - face quality / shot type (close_up > medium > wide > no_face)
  - subject_change penalty
And mark the highest-scored take as the "best".
"""

from __future__ import annotations

import re
from typing import Any


_WORD_RE = re.compile(r"[a-zà-ÿ0-9]+", re.IGNORECASE)


def _shingle(text: str, *, max_tokens: int = 30, n: int = 3) -> set[tuple[str, ...]]:
    tokens = _WORD_RE.findall((text or "").lower())[:max_tokens]
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def group_takes(clips: list[dict[str, Any]], *, threshold: float = 0.45) -> list[list[str]]:
    """Returns a list of groups (each a list of clip IDs). Singletons are
    omitted — only multi-take groups are returned."""
    sigs = {c["id"]: _shingle(c.get("transcript_text", "")) for c in clips}
    parent: dict[str, str] = {c["id"]: c["id"] for c in clips}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    cids = list(sigs.keys())
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            if _jaccard(sigs[cids[i]], sigs[cids[j]]) >= threshold:
                union(cids[i], cids[j])

    groups: dict[str, list[str]] = {}
    for cid in cids:
        groups.setdefault(find(cid), []).append(cid)
    return [g for g in groups.values() if len(g) > 1]


def score_take(clip: dict[str, Any]) -> float:
    """Higher = better take."""
    score = 0.0
    fa = clip.get("face_analysis") or {}
    shot = fa.get("shot_type", "no_face")
    if shot == "close_up":
        score += 3.0
    elif shot == "medium":
        score += 1.5
    elif shot == "wide":
        score += 0.5
    else:
        score -= 1.0
    if fa.get("subject_change"):
        score -= 1.5
    qc = clip.get("quality_check") or {}
    q = qc.get("quality")
    if q and q != "ok":
        score -= 1.5
    if qc.get("brightness", 0) > 50 and qc.get("brightness", 0) < 200:
        score += 0.5
    # transcript length proxy: more meaningful words = more usable take
    tt = (clip.get("transcript_text") or "").strip()
    score += min(2.0, len(tt) / 200.0)
    return round(score, 2)


def annotate_groups(clips: list[dict[str, Any]], groups: list[list[str]]) -> list[dict[str, Any]]:
    """Returns [{"group": [id1, id2, ...], "best": id_of_best, "scores": {id: score}}]"""
    by_id = {c["id"]: c for c in clips}
    out: list[dict[str, Any]] = []
    for g in groups:
        scores = {cid: score_take(by_id[cid]) for cid in g if cid in by_id}
        if not scores:
            continue
        best = max(scores.items(), key=lambda kv: kv[1])[0]
        out.append({
            "group": list(scores.keys()),
            "best": best,
            "scores": scores,
        })
    return out
