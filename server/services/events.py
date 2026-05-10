"""In-memory pub/sub for project events, exposed to the UI over SSE.

Endpoints publish stage updates and percent progress to a per-project topic;
the SPA subscribes via EventSource and updates the pipeline cards in real time.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, project_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers[project_id].add(q)
        return q

    def unsubscribe(self, project_id: str, q: asyncio.Queue) -> None:
        self._subscribers[project_id].discard(q)
        if not self._subscribers[project_id]:
            self._subscribers.pop(project_id, None)

    def publish(self, project_id: str, event: dict[str, Any]) -> None:
        event = {**event, "ts": time.time()}
        for q in list(self._subscribers.get(project_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # subscriber too slow — drop the oldest event to make room
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


bus = EventBus()


def emit_stage(project_id: str, name: str, status: str, message: str | None = None, *, progress: float | None = None) -> None:
    bus.publish(project_id, {
        "type": "stage",
        "stage": name,
        "status": status,
        "message": message,
        "progress": progress,
    })


def emit_log(project_id: str, message: str, *, level: str = "info") -> None:
    bus.publish(project_id, {"type": "log", "level": level, "message": message})


def emit_state_changed(project_id: str) -> None:
    bus.publish(project_id, {"type": "state"})
