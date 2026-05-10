"""Run `npx hyperframes render` against a generated composition."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class RenderError(RuntimeError):
    pass


async def render(composition_dir: Path, *, output_dir: Path, name: str) -> Path:
    """Render and return the path to the produced MP4."""
    output_dir.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "npx",
        "--yes",
        "hyperframes@0.5.5",
        "render",
        cwd=str(composition_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode(errors="ignore")
    err = stderr.decode(errors="ignore")
    if proc.returncode != 0:
        raise RenderError(f"hyperframes render failed (exit {proc.returncode})\n{out}\n{err}")

    renders_dir = composition_dir / "renders"
    if not renders_dir.exists():
        raise RenderError(f"renders/ not produced. log:\n{out}\n{err}")
    candidates = sorted(renders_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RenderError("no mp4 produced")
    src = candidates[0]
    dst = output_dir / f"{name}.mp4"
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), dst)
    return dst
