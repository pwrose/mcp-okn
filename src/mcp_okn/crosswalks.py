"""Precomputed, hand-verified cross-KG join recipes for the FRINK federation.

`find_crosswalks` discovers join keys LIVE — it fires federation scans that time
out on large graphs, so it is unreliable. This module instead serves a curated
static table (``data/crosswalks.json``) of join recipes that were verified with
exact ``COUNT(DISTINCT)`` over the named graphs on a known date. For a KG pair it
answers one of three states:

  * ``verified``  — here is the ready join recipe (predicates, roles, shared key,
    IRI-normalization snippet, bridge, verified count);
  * ``known_non_join`` — this pair was checked and does NOT join on the obvious
    key; don't waste a query, here's why;
  * ``unknown`` — nothing precomputed; fall back to `find_crosswalks`.

The file's own consumer contract: keyed by a KG shortname, surface every entry
where it appears as ``left_kg``, ``right_kg``, ``bridge_kg``, or in a ``members``
clique.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

# Process-lifetime cache (the table is static and changes rarely).
_data_cache: dict[str, Any] | None = None


def load_crosswalks() -> dict[str, Any]:
    """Load the bundled static crosswalk table.

    Returns an empty dict if the file is missing or unreadable, so callers
    degrade to the live `find_crosswalks` path instead of erroring.
    """
    global _data_cache
    if _data_cache is not None:
        return _data_cache
    try:
        text = (
            resources.files("mcp_okn")
            .joinpath("data", "crosswalks.json")
            .read_text(encoding="utf-8")
        )
        data = json.loads(text)
        _data_cache = data if isinstance(data, dict) else {}
    except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError, OSError):
        _data_cache = {}
    return _data_cache


def _entry_kgs(entry: dict[str, Any]) -> set[str]:
    """Every KG shortname an entry touches (left/right/bridge + members)."""
    kgs: set[str] = set()
    for key in ("left_kg", "right_kg", "bridge_kg"):
        if entry.get(key):
            kgs.add(entry[key])
    kgs.update(entry.get("members", []))
    return kgs


def _nonjoin_kgs(entry: dict[str, Any]) -> set[str]:
    """Every KG shortname a known-non-join record references."""
    kgs: set[str] = set()
    for key in ("left_kg", "right_kg", "kg"):
        if entry.get(key):
            kgs.add(entry[key])
    return kgs


def verified_for(shortname: str) -> list[dict[str, Any]]:
    """All verified join entries that touch ``shortname`` (any role)."""
    data = load_crosswalks()
    return [
        e
        for e in data.get("verified_crosswalks", [])
        if shortname in _entry_kgs(e)
    ]


def all_crosswalks(include_examples: bool = True) -> list[dict[str, Any]]:
    """Compact summary of every verified cross-KG integration point.

    One row per verified crosswalk: its id, the KGs it connects (left/right/
    bridge + clique members, sorted), the shared identifier, the bridge KG if
    any, and the verified row count. ``example_question`` is included unless
    ``include_examples`` is False.
    """
    rows: list[dict[str, Any]] = []
    for e in load_crosswalks().get("verified_crosswalks", []):
        row = {
            "id": e.get("id"),
            "kgs": sorted(_entry_kgs(e)),
            "shared_key": e.get("shared_key"),
            "bridge_kg": e.get("bridge_kg"),
            "verified_count": e.get("verified_count"),
        }
        if include_examples:
            row["example_question"] = e.get("example_question")
        rows.append(row)
    return rows


def join_between(kg_a: str, kg_b: str) -> list[dict[str, Any]]:
    """Verified entries that connect ``kg_a`` and ``kg_b`` (order-insensitive).

    A bridged entry counts as connecting the two endpoints even when one of them
    is named only as the ``bridge_kg``.
    """
    out: list[dict[str, Any]] = []
    for e in load_crosswalks().get("verified_crosswalks", []):
        kgs = _entry_kgs(e)
        if kg_a in kgs and kg_b in kgs:
            out.append(e)
    return out


def nonjoin_for(shortname: str) -> list[dict[str, Any]]:
    """Known-non-join records that reference ``shortname``."""
    data = load_crosswalks()
    return [
        e
        for e in data.get("known_non_joins", [])
        if shortname in _nonjoin_kgs(e)
    ]


def nonjoin_between(kg_a: str, kg_b: str) -> list[dict[str, Any]]:
    """Known-non-join records that reference BOTH KGs (order-insensitive).

    Single-KG records (an unmaterialized / schema-only graph) are returned when
    either endpoint is that KG, since they explain why no join is possible.
    """
    out: list[dict[str, Any]] = []
    for e in load_crosswalks().get("known_non_joins", []):
        kgs = _nonjoin_kgs(e)
        if {kg_a, kg_b} <= kgs:
            out.append(e)
        elif "kg" in e and e["kg"] in (kg_a, kg_b):
            out.append(e)
    return out


def island_status(shortname: str) -> dict[str, Any] | None:
    """Island / thin-thread context for ``shortname``, or None if neither.

    Returns ``{"island": bool, "thin_threads": [...], "note": ...}`` when the KG
    is a profiled island or has documented thin threads, so a caller can warn
    that public join keys are scarce.
    """
    islands = load_crosswalks().get("islands", {})
    is_island = shortname in islands.get("kgs", [])
    threads = [t for t in islands.get("thin_threads", []) if t.startswith(shortname)]
    if not is_island and not threads:
        return None
    return {
        "island": is_island,
        "thin_threads": threads,
        "note": islands.get("note"),
    }


def verified_on() -> str | None:
    """The date the table's counts were verified, for staleness visibility."""
    return load_crosswalks().get("verified_on")
