"""Discovery of Proto-OKN knowledge graphs via the okn-registry.

Each KG has a markdown file with YAML frontmatter in the frink-okn/okn-registry
repo. We use those descriptions purely to help choose which named graph(s) to
query on the *federation* endpoint. The per-KG ``sparql:``/``tpf:`` endpoints in
the frontmatter are intentionally NOT exposed as query targets.
"""

from __future__ import annotations

import asyncio
import json
from importlib import resources
from typing import Any

import httpx
import yaml

from .sparql import named_graph

_CONTENTS_API = (
    "https://api.github.com/repos/frink-okn/okn-registry/"
    "contents/docs/registry/kgs"
)
_RAW_BASE = (
    "https://raw.githubusercontent.com/frink-okn/okn-registry/"
    "refs/heads/main/docs/registry/kgs/{shortname}.md"
)

# KGs listed in the registry but excluded from results — e.g. not loaded in the
# federation under their expected named graph (queries return no rows).
EXCLUDED_KGS = {"semopenalex", "biohealth"}

# Process-lifetime caches (the registry changes rarely).
_shortnames_cache: list[str] | None = None
_meta_cache: dict[str, dict[str, Any]] = {}
_doc_cache: dict[str, str] = {}


def load_snapshot() -> list[dict[str, Any]]:
    """Load the bundled static KG snapshot (for instant cold starts).

    Returns an empty list if the snapshot is missing or unreadable, so callers
    can fall back to a live registry fetch.
    """
    try:
        text = (
            resources.files("mcp_okn")
            .joinpath("data", "kgs.json")
            .read_text(encoding="utf-8")
        )
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError, OSError):
        return []


def _raw_url(shortname: str) -> str:
    return _RAW_BASE.format(shortname=shortname)


def _split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body) from a markdown file with `---` fences."""
    if markdown.startswith("---"):
        parts = markdown.split("---", 2)
        if len(parts) == 3:
            try:
                front = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                front = {}
            return (front if isinstance(front, dict) else {}, parts[2].strip())
    return {}, markdown.strip()


def _meta_from_front(shortname: str, front: dict[str, Any]) -> dict[str, Any]:
    """Project frontmatter to the safe, query-relevant metadata surface.

    Note: ``sparql`` and ``tpf`` are deliberately dropped so the per-KG Jena
    endpoints are never surfaced as query targets.
    """
    return {
        "shortname": front.get("shortname", shortname),
        "title": front.get("title", shortname),
        "description": (front.get("description") or "").strip(),
        "homepage": front.get("homepage"),
        "named_graph": named_graph(front.get("shortname", shortname)),
    }


async def list_kg_shortnames(
    client: httpx.AsyncClient | None = None, refresh: bool = False
) -> list[str]:
    """List KG shortnames from the registry directory (cached)."""
    global _shortnames_cache
    if _shortnames_cache is not None and not refresh:
        return _shortnames_cache

    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.get(
            _CONTENTS_API, headers={"Accept": "application/vnd.github+json"}
        )
        resp.raise_for_status()
        entries = resp.json()
    finally:
        if owns:
            await client.aclose()

    names = sorted(
        e["name"][:-3]
        for e in entries
        if isinstance(e, dict)
        and e.get("name", "").endswith(".md")
        and e["name"][:-3] not in EXCLUDED_KGS
    )
    _shortnames_cache = names
    return names


async def fetch_kg_meta(
    shortname: str, client: httpx.AsyncClient | None = None, refresh: bool = False
) -> dict[str, Any]:
    """Fetch and parse one KG's frontmatter into safe metadata (cached)."""
    if shortname in _meta_cache and not refresh:
        return _meta_cache[shortname]

    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.get(_raw_url(shortname))
        resp.raise_for_status()
        raw = resp.text
        front, _body = _split_frontmatter(raw)
    finally:
        if owns:
            await client.aclose()

    meta = _meta_from_front(shortname, front)
    _meta_cache[shortname] = meta
    _doc_cache[shortname] = raw  # populate doc cache opportunistically
    return meta


async def list_kgs(refresh: bool = False) -> list[dict[str, Any]]:
    """Return metadata for every KG.

    By default this serves the bundled static snapshot for an instant cold
    start. Pass ``refresh=True`` to re-fetch the live registry (and rebuild the
    in-process caches). If the snapshot is unavailable, falls back to a live
    fetch automatically.
    """
    if not refresh:
        snapshot = load_snapshot()
        if snapshot:
            return snapshot

    async with httpx.AsyncClient(timeout=30.0) as client:
        names = await list_kg_shortnames(client=client, refresh=refresh)
        metas = await asyncio.gather(
            *(fetch_kg_meta(n, client=client, refresh=refresh) for n in names),
            return_exceptions=True,
        )
    result: list[dict[str, Any]] = []
    for name, meta in zip(names, metas):
        if isinstance(meta, Exception):
            result.append(
                {
                    "shortname": name,
                    "title": name,
                    "description": f"(failed to load registry entry: {meta})",
                    "named_graph": named_graph(name),
                }
            )
        else:
            result.append(meta)
    return result


async def fetch_kg_doc(
    shortname: str, client: httpx.AsyncClient | None = None, refresh: bool = False
) -> str:
    """Return the full registry markdown (frontmatter + prose) for one KG."""
    if shortname in _doc_cache and not refresh:
        return _doc_cache[shortname]

    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.get(_raw_url(shortname))
        resp.raise_for_status()
    finally:
        if owns:
            await client.aclose()

    _doc_cache[shortname] = resp.text
    return resp.text
