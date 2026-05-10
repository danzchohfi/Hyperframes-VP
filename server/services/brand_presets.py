"""Server-wide brand-book presets (saved at server/projects/_presets/)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "projects" / "_presets"
ROOT.mkdir(parents=True, exist_ok=True)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", (name or "preset").lower()).strip("-")
    return s or "preset"


def list_presets() -> list[dict[str, Any]]:
    out = []
    for child in sorted(ROOT.glob("*.json")):
        try:
            data = json.loads(child.read_text())
            data["_id"] = child.stem
            out.append(data)
        except Exception:
            continue
    return out


def save_preset(name: str, brand: dict[str, Any]) -> dict[str, Any]:
    pid = _slug(name)
    path = ROOT / f"{pid}.json"
    payload = {**brand, "name": name}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    payload["_id"] = pid
    return payload


def get_preset(pid: str) -> dict[str, Any]:
    path = ROOT / f"{pid}.json"
    if not path.exists():
        raise FileNotFoundError(pid)
    data = json.loads(path.read_text())
    data["_id"] = pid
    return data


def delete_preset(pid: str) -> bool:
    path = ROOT / f"{pid}.json"
    if path.exists():
        path.unlink()
        return True
    return False
