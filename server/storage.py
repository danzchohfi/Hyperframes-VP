"""Filesystem-backed project store.

Each project lives in `server/projects/<id>/` with this layout:

  state.json       - ProjectState (status, refs to artifacts)
  source.<ext>     - the originally uploaded video
  source.mp4       - normalized H.264 copy used by the pipeline
  transcript.json  - Whisper word-level transcript
  cuts.json        - silence-cut decision list
  cut.mp4          - silence-cut intermediate
  graded.mp4       - LUT-applied intermediate
  brand.json       - BrandBook
  lut.cube         - uploaded LUT (optional)
  composition/     - generated Hyperframes project
  exports/         - rendered MP4 outputs
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


class Stage(BaseModel):
    name: str
    status: str = "pending"  # pending | running | done | error
    message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ProjectState(BaseModel):
    id: str
    name: str
    created_at: str = Field(default_factory=lambda: _now())
    updated_at: str = Field(default_factory=lambda: _now())
    source_filename: str | None = None
    source_duration: float | None = None
    has_transcript: bool = False
    has_cuts: bool = False
    has_lut: bool = False
    lut_filename: str | None = None
    has_brand: bool = False
    has_render: bool = False
    last_export: str | None = None
    angles: list[dict[str, Any]] = Field(default_factory=list)  # [{name, filename, duration, tags?}, ...]
    music_suggestion: dict[str, Any] | None = None
    music_track: dict[str, Any] | None = None
    has_fillers: bool = False
    fillers_count: int = 0
    has_soundbites: bool = False
    has_story: bool = False
    has_roughcut: bool = False
    stages: dict[str, Stage] = Field(default_factory=dict)

    def stage(self, name: str) -> Stage:
        if name not in self.stages:
            self.stages[name] = Stage(name=name)
        return self.stages[name]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_project_id() -> str:
    return f"p_{int(time.time())}_{secrets.token_hex(3)}"


def project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def state_path(project_id: str) -> Path:
    return project_dir(project_id) / "state.json"


def load(project_id: str) -> ProjectState:
    p = state_path(project_id)
    if not p.exists():
        raise FileNotFoundError(project_id)
    return ProjectState.model_validate_json(p.read_text())


def save(state: ProjectState) -> None:
    state.updated_at = _now()
    state_path(state.id).write_text(state.model_dump_json(indent=2))


def create(name: str) -> ProjectState:
    pid = new_project_id()
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    (project_dir(pid) / "exports").mkdir(exist_ok=True)
    state = ProjectState(id=pid, name=name or pid)
    save(state)
    return state


def list_projects() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for child in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        sp = child / "state.json"
        if sp.exists():
            try:
                s = ProjectState.model_validate_json(sp.read_text())
                out.append(
                    {
                        "id": s.id,
                        "name": s.name,
                        "updated_at": s.updated_at,
                        "has_render": s.has_render,
                    }
                )
            except Exception:
                continue
    return out


def write_json(project_id: str, name: str, data: Any) -> Path:
    p = project_dir(project_id) / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return p


def read_json(project_id: str, name: str) -> Any:
    p = project_dir(project_id) / name
    return json.loads(p.read_text())
