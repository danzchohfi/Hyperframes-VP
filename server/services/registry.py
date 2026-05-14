"""Hyperframes community registry — catalog browse + install.

Wraps `npx hyperframes catalog --json` (browse) and
`npx hyperframes add <name> --dir <project>` (install) so the web app
can expose them without the user touching a terminal.

The catalog is fetched from the Hyperframes registry over the network
and cached in memory for `CATALOG_TTL_SECONDS`. The registry URL is
fixed in the CLI (it doesn't take a --registry flag at catalog time),
so caching here is purely a latency / rate-limit optimization.

Installed blocks land at:
  <composition>/compositions/<name>.html         (blocks)
  <composition>/compositions/components/<name>.html  (components)

The CLI returns a JSON envelope on install with the snippet to include
in index.html. We surface that to the client so the UI can show the
exact paste-in for advanced users who want to place a block manually.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 5 * 60   # 5 min — registry rarely changes
HYPERFRAMES_VERSION = "0.5.5"  # pinned; matches package.json
_NPX_TIMEOUT = 120.0


class RegistryError(RuntimeError):
    pass


# In-memory cache for the catalog. Single-process, single-tenant —
# fine for this app's scale. Tuple of (timestamp, parsed_list).
_catalog_cache: tuple[float, list[dict[str, Any]]] | None = None


async def _run_npx(args: list[str], *, cwd: Path | None = None, timeout: float = _NPX_TIMEOUT) -> str:
    """Run `npx --yes hyperframes@VER <args>`. Returns stdout. Raises
    RegistryError on non-zero exit, with stderr included in the message."""
    cmd = ["npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RegistryError(f"hyperframes {args[0] if args else '?'}: timeout após {timeout}s")
    if proc.returncode != 0:
        msg = err_b.decode(errors="ignore").strip() or out_b.decode(errors="ignore").strip()
        raise RegistryError(f"hyperframes {' '.join(shlex.quote(a) for a in args)}: {msg}")
    return out_b.decode(errors="ignore")


async def fetch_catalog(*, force: bool = False) -> list[dict[str, Any]]:
    """Return the community registry catalog. Cached for CATALOG_TTL_SECONDS.

    Each item has {name, type, title, description, tags, dimensions?,
    duration?} — `dimensions` and `duration` only on blocks (components
    don't have intrinsic timing). See:
      npx hyperframes catalog --json
    """
    global _catalog_cache
    now = time.time()
    if not force and _catalog_cache is not None:
        ts, items = _catalog_cache
        if now - ts < CATALOG_TTL_SECONDS:
            return items

    out = await _run_npx(["catalog", "--json"])
    try:
        items = json.loads(out)
    except json.JSONDecodeError as e:
        raise RegistryError(f"resposta inválida do catalog: {e}")
    if not isinstance(items, list):
        raise RegistryError("catalog não retornou uma lista")
    _catalog_cache = (now, items)
    return items


async def install_block(name: str, *, composition_dir: Path) -> dict[str, Any]:
    """Install a block or component into the project's composition dir.

    Returns the parsed JSON envelope:
      {ok, name, type, typeDir, written: [paths], snippet, clipboardCopied}

    `snippet` is the HTML snippet the user (or composer) inserts at the
    desired position in index.html — for blocks it's a
    <div data-composition-src=...> with the block's natural duration
    and dimensions; for components it varies.
    """
    if not composition_dir.exists():
        raise RegistryError(f"diretório de composição não existe: {composition_dir}")
    if not (composition_dir / "hyperframes.json").exists():
        raise RegistryError(
            "composition/ não tem hyperframes.json — rode a render uma vez "
            "(ou abra o Preview Hyperframes) pra materializar a composição"
        )
    # Length-limited; only [a-z0-9-] should appear in registry names. We
    # don't sanitize aggressively because npx will reject bad names — the
    # check here is just defensive against argument injection from the
    # request body.
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise RegistryError(f"nome inválido: {name!r}")

    out = await _run_npx(
        ["add", name, "--dir", str(composition_dir), "--no-clipboard", "--json"],
        cwd=composition_dir,
    )
    # The CLI prints a one-liner JSON. Some warnings may leak to stderr
    # (which we already drop) — stdout should be clean JSON.
    try:
        return json.loads(out.strip())
    except json.JSONDecodeError as e:
        raise RegistryError(f"resposta inválida do add: {e} | raw={out[:200]}")


def list_installed(composition_dir: Path) -> list[dict[str, Any]]:
    """List blocks and components currently in the composition dir.

    Reads file names under compositions/ and compositions/components/.
    Cross-references against the cached catalog (if available) to enrich
    each entry with title/description/tags. Items not in the catalog get
    minimal metadata — they may be hand-rolled blocks the user added.
    """
    items: list[dict[str, Any]] = []
    if not composition_dir.exists():
        return items
    by_name: dict[str, dict[str, Any]] = {}
    if _catalog_cache is not None:
        for it in _catalog_cache[1]:
            by_name[it.get("name", "")] = it

    blocks_dir = composition_dir / "compositions"
    if blocks_dir.exists():
        for f in sorted(blocks_dir.glob("*.html")):
            name = f.stem
            meta = by_name.get(name) or {"name": name, "type": "block"}
            items.append({**meta, "path": f"compositions/{f.name}", "kind": "block"})
        components_dir = blocks_dir / "components"
        if components_dir.exists():
            for f in sorted(components_dir.glob("*.html")):
                name = f.stem
                meta = by_name.get(name) or {"name": name, "type": "component"}
                items.append({**meta, "path": f"compositions/components/{f.name}", "kind": "component"})
    return items


def uninstall(name: str, *, composition_dir: Path) -> bool:
    """Delete an installed block / component by name. Returns True if a
    file was removed. Path-traversal-safe (rejects names with /, .., \\)."""
    if not name or any(c in name for c in "/.\\"):
        raise RegistryError(f"nome inválido: {name!r}")
    candidates = [
        composition_dir / "compositions" / f"{name}.html",
        composition_dir / "compositions" / "components" / f"{name}.html",
    ]
    removed = False
    for c in candidates:
        if c.exists():
            try:
                c.unlink()
                removed = True
            except Exception as e:
                raise RegistryError(f"falha ao remover {c.name}: {e}")
    return removed


def npx_available() -> bool:
    """Whether `npx` is on PATH. If not, registry endpoints return 503."""
    return shutil.which("npx") is not None
