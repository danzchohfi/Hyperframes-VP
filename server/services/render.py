"""Run `npx hyperframes render` against a generated composition.

Streams progress events back via the optional `on_event` callback so the SSE
event bus can update the UI in real time.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Awaitable, Callable

OnEvent = Callable[[dict], None] | Callable[[dict], Awaitable[None]]

_PROGRESS_RE = re.compile(r"(\d{1,3})%\s+([A-Za-z][^\s].*?)(?:\s|$)")


class RenderError(RuntimeError):
    pass


async def render(
    composition_dir: Path,
    *,
    output_dir: Path,
    name: str,
    on_event: OnEvent | None = None,
) -> Path:
    """Render and return the path to the produced MP4."""
    output_dir.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "npx", "--yes", "hyperframes@0.5.5", "render",
        cwd=str(composition_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    out_lines: list[str] = []
    last_progress: int = -1

    async def _emit(ev: dict) -> None:
        if on_event is None:
            return
        try:
            res = on_event(ev)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass

    assert proc.stdout is not None
    while True:
        chunk = await proc.stdout.readline()
        if not chunk:
            break
        line = chunk.decode(errors="ignore").rstrip("\r\n")
        if not line:
            continue
        out_lines.append(line)
        # Hyperframes prints lines like "  ███░░  35%  Capturing frame 60/240"
        m = _PROGRESS_RE.search(line)
        if m:
            pct = int(m.group(1))
            label = m.group(2).strip()
            if pct != last_progress:
                last_progress = pct
                await _emit({"progress": pct, "label": label})
        elif "Render complete" in line:
            await _emit({"progress": 100, "label": "Render complete"})

    rc = await proc.wait()
    out = "\n".join(out_lines)
    if rc != 0:
        raise RenderError(f"hyperframes render failed (exit {rc})\n{out}")

    renders_dir = composition_dir / "renders"
    if not renders_dir.exists():
        raise RenderError(f"renders/ not produced. log:\n{out}")
    candidates = sorted(renders_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RenderError("no mp4 produced")
    src = candidates[0]
    dst = output_dir / f"{name}.mp4"
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), dst)
    return dst
