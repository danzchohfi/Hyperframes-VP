"""Async job queue with persistent history per project.

Lets the UI fire-and-forget long-running ops (transcribe, apply, roughcut,
render) and poll/stream progress. Jobs persist to <project>/jobs.json so
they survive restarts.

This is intentionally simple: in-process asyncio tasks, no Redis/Celery.
For multi-process scale, swap _runner for a real queue.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from . import events as events_svc
from .. import storage


JobFn = Callable[["JobContext"], Awaitable[Any]]


class JobContext:
    """Passed to the job function. Provides progress + log hooks."""

    def __init__(self, manager: "JobManager", project_id: str, job_id: str, name: str):
        self.manager = manager
        self.project_id = project_id
        self.job_id = job_id
        self.name = name
        self._cancel_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    async def cancel(self) -> None:
        self._cancel_event.set()
        if self._task:
            self._task.cancel()

    def progress(self, value: float, label: str | None = None) -> None:
        self.manager._update(self, status="running", progress=value, message=label)

    def log(self, line: str, level: str = "info") -> None:
        events_svc.emit_log(self.project_id, f"[{self.name}] {line}", level=level)

    def check_cancel(self) -> None:
        if self.cancelled():
            raise asyncio.CancelledError(f"job {self.job_id} cancelled")


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobContext] = {}
        self._state: dict[str, dict[str, Any]] = {}  # job_id → snapshot dict

    def list(self, project_id: str) -> list[dict[str, Any]]:
        items = storage.get_render_history(project_id)  # not used; just to ensure import
        return _read_history(project_id)

    def _persist(self) -> None:
        # group jobs per project and persist
        per_project: dict[str, list[dict[str, Any]]] = {}
        for snap in self._state.values():
            per_project.setdefault(snap["project_id"], []).append(snap)
        for pid, entries in per_project.items():
            try:
                # merge with existing file (older jobs we no longer track in memory)
                existing = _read_history(pid)
                by_id = {e["id"]: e for e in existing}
                for snap in entries:
                    by_id[snap["id"]] = snap
                merged = sorted(by_id.values(), key=lambda e: e.get("created_at") or 0)[-200:]
                storage.write_json(pid, "jobs.json", merged)
            except Exception:
                continue

    def _update(self, ctx: JobContext, **fields: Any) -> None:
        snap = self._state.setdefault(ctx.job_id, {
            "id": ctx.job_id,
            "project_id": ctx.project_id,
            "name": ctx.name,
            "status": "pending",
            "progress": 0.0,
            "message": None,
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": None,
        })
        for k, v in fields.items():
            if v is not None:
                snap[k] = v
        snap["updated_at"] = time.time()
        self._persist()
        events_svc.bus.publish(ctx.project_id, {
            "type": "job",
            "job_id": ctx.job_id,
            "name": ctx.name,
            **{k: v for k, v in snap.items() if k != "project_id"},
        })

    async def submit(self, project_id: str, name: str, fn: JobFn) -> str:
        job_id = f"job_{int(time.time())}_{secrets.token_hex(3)}"
        ctx = JobContext(self, project_id, job_id, name)
        self._jobs[job_id] = ctx
        self._update(ctx, status="pending", message="queued")

        async def runner() -> None:
            self._update(ctx, status="running", progress=0.0, message="started")
            try:
                result = await fn(ctx)
                self._update(ctx, status="done", progress=1.0,
                             message="finished", result=result)
            except asyncio.CancelledError:
                self._update(ctx, status="cancelled", message="cancelled")
            except Exception as e:
                events_svc.emit_log(ctx.project_id, traceback.format_exc(), level="error")
                self._update(ctx, status="error", message=str(e), error=str(e))
            finally:
                self._jobs.pop(job_id, None)

        task = asyncio.create_task(runner())
        ctx._task = task
        return job_id

    async def cancel(self, job_id: str) -> bool:
        ctx = self._jobs.get(job_id)
        if not ctx:
            return False
        await ctx.cancel()
        return True

    def get(self, project_id: str, job_id: str) -> dict[str, Any] | None:
        entries = _read_history(project_id)
        for e in entries:
            if e.get("id") == job_id:
                return e
        snap = self._state.get(job_id)
        if snap and snap.get("project_id") == project_id:
            return snap
        return None


def _read_history(project_id: str) -> list[dict[str, Any]]:
    pdir = storage.project_dir(project_id)
    p = pdir / "jobs.json"
    if not p.exists():
        return []
    try:
        import json
        return json.loads(p.read_text())
    except Exception:
        return []


# singleton
manager = JobManager()
