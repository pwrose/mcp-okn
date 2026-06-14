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


# The prose recipe is dropped from query-facing output in favour of the runnable
# skeleton_query, which encodes the same IRI-normalization executably.
_RECIPE_ONLY_FIELDS = ("iri_normalization",)


def for_query(entry: dict[str, Any]) -> dict[str, Any]:
    """Project a verified crosswalk for query guidance.

    Returns a copy with the prose recipe (``iri_normalization``) removed: the
    bundled ``skeleton_query`` is a verified, runnable example that already
    encodes the same normalization, so it — not the prose — is what guides a
    caller writing SPARQL. A ``domain`` field is added (see :func:`domain_for`)
    so a multi-join listing groups consistently with ``list_crosswalks``.
    """
    out = {k: v for k, v in entry.items() if k not in _RECIPE_ONLY_FIELDS}
    out["domain"] = domain_for(entry.get("shared_key"))
    return out


def _listing_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """Sort joins by (domain, shared_key, id) so a listing groups by domain."""
    return (row.get("domain", ""), row.get("shared_key") or "", row.get("id") or "")


def verified_for(shortname: str) -> list[dict[str, Any]]:
    """All verified join entries that touch ``shortname`` (any role), grouped by
    domain (sorted by ``(domain, shared_key)``)."""
    data = load_crosswalks()
    out = [
        for_query(e)
        for e in data.get("verified_crosswalks", [])
        if shortname in _entry_kgs(e)
    ]
    out.sort(key=_listing_sort_key)
    return out


def _ordered_kgs(entry: dict[str, Any]) -> list[str]:
    """KGs of an entry in join order: left → bridge → right.

    The bridge graph (e.g. ``ubergraph``) sits in the MIDDLE — it is what the two
    endpoints meet through — so a plain alphabetical sort would misleadingly push
    it to one end. Clique entries (``members``, no left/right) keep sorted order.
    """
    if entry.get("members"):
        return sorted(entry["members"])
    ordered = [entry.get("left_kg"), entry.get("bridge_kg"), entry.get("right_kg")]
    return [kg for kg in ordered if kg]


# Group each crosswalk into a domain by its shared identifier, so the listing
# renders as a table organised by domain. Keep this in sync with the table's
# shared_key vocabulary (a test asserts every key is mapped).
_DOMAIN_BY_SHARED_KEY: dict[str, str] = {
    "DOID": "Disease & phenotype",
    "MONDO": "Disease & phenotype",
    "HP": "Disease & phenotype",
    "DOID<->MONDO": "Disease & phenotype",
    "MONDO<->OMIM (bridged)": "Disease & phenotype",
    "MONDO<->Orphanet (bridged)": "Disease & phenotype",
    "MONDO<->DOID (bridged)": "Disease & phenotype",
    "MeSH_descriptor_id": "Disease & phenotype",
    "Ensembl": "Genes",
    "Entrez": "Genes",
    "HGNC -> Entrez (bridged)": "Genes",
    "UniProt": "Proteins",
    "CAS": "Chemicals",
    "CHEBI<->CAS": "Chemicals",
    "PubChem CID": "Chemicals",
    "NCBITaxon": "Taxonomy",
    "S2_L13": "Geospatial",
    "county_FIPS": "Geospatial",
    "state_FIPS": "Geospatial",
    "KWG_county": "Geospatial",
    "ZIP5": "Geospatial",
    "NAICS": "Industry & supply chain",
    "SUDOKN_industry_sector": "Industry & supply chain",
}


def domain_for(shared_key: str | None) -> str:
    """The domain a crosswalk belongs to, keyed by its shared identifier."""
    return _DOMAIN_BY_SHARED_KEY.get(shared_key or "", "Other")


def _is_taxon_hub_spoke(entry: dict[str, Any]) -> bool:
    """True for a KG↔ubergraph NCBITaxon *spoke* — collapsed into a single hub row
    by :func:`all_crosswalks` so the listing speaks in integration terms, not in
    each KG's (uninteresting) overlap with the ubergraph hub.

    A pairwise taxon crosswalk that merely *bridges through* ubergraph (ubergraph
    is the ``bridge_kg``, e.g. spoke-genelab↔spoke-okn / D9) is NOT a spoke: it is a
    real KG-to-KG integration point and keeps its own row.
    """
    return (
        entry.get("shared_key") == "NCBITaxon"
        and not entry.get("bridge_kg")
        and "ubergraph" in (entry.get("left_kg"), entry.get("right_kg"))
    )


def all_crosswalks(include_examples: bool = True) -> list[dict[str, Any]]:
    """Compact summary of every verified cross-KG integration point.

    One row per verified crosswalk: its ``domain`` (e.g. "Genes", "Geospatial"),
    the KGs it connects in join order (left → bridge → right, by official registry
    shortname), the shared identifier, the bridge KG if any, and the verified row
    count. ``example_question`` is included unless ``include_examples`` is False.

    The NCBITaxon crosswalks are a HUB (each KG joins ``ubergraph``), but a user
    cares about pairwise integration, not each KG's overlap with the ubergraph
    plumbing. So the per-KG ``KG↔ubergraph`` spokes are collapsed into ONE hub row
    (``hub: "ubergraph"``) that names the mutually-integratable member KGs and
    points to ``taxon_overlap(kg_a, kg_b)`` for a pair's counts (which are
    two-valued — exact id vs clade — so no single pairwise number is shown). The
    underlying spoke recipes are untouched and still served by
    ``get_join_strategy`` / ``verified_for``; only this listing collapses them.
    Verified pairwise taxon crosswalks that bridge through ubergraph (e.g. D9) keep
    their own rows.

    Rows are sorted by ``(domain, shared_key, kgs)`` so the result reads as a
    table grouped by domain and ordered by ontology within each — ready to render
    directly.

    The internal table ``id`` is deliberately omitted: it embeds KG abbreviations
    (e.g. ``M2-mesh-spokeokn``) that are NOT official shortnames, so a listing
    keyed on it would misname KGs. Callers identify a crosswalk by its ``kgs``
    (official shortnames) and ``shared_key``.
    """
    rows: list[dict[str, Any]] = []
    hub_members: set[str] = set()
    for e in load_crosswalks().get("verified_crosswalks", []):
        if _is_taxon_hub_spoke(e):
            hub_members.update(
                kg
                for kg in (e.get("left_kg"), e.get("right_kg"))
                if kg and kg != "ubergraph"
            )
            continue
        row = {
            "domain": domain_for(e.get("shared_key")),
            "kgs": _ordered_kgs(e),
            "shared_key": e.get("shared_key"),
            "bridge_kg": e.get("bridge_kg"),
            "verified_count": e.get("verified_count"),
        }
        if include_examples:
            row["example_question"] = e.get("example_question")
        rows.append(row)

    if hub_members:
        cluster: dict[str, Any] = {
            "domain": domain_for("NCBITaxon"),
            "kgs": sorted(hub_members),
            "shared_key": "NCBITaxon",
            "bridge_kg": None,
            "verified_count": None,
            "hub": "ubergraph",
            "note": (
                "NCBITaxon hub: these KGs each map organisms to the ubergraph "
                "taxonomy and are pairwise-integratable through it. Pairwise "
                "overlap is two-valued (exact id vs clade membership) — call "
                "taxon_overlap(kg_a, kg_b) for a pair's counts. Verified pairwise "
                "taxon crosswalks (e.g. spoke-genelab<->spoke-okn) keep their own "
                "rows."
            ),
        }
        if include_examples:
            cluster["example_question"] = (
                "Which KGs share organisms, and how many? Use "
                "taxon_overlap(kg_a, kg_b) for any pair."
            )
        rows.append(cluster)

    rows.sort(key=lambda r: (r["domain"], r["shared_key"] or "", r["kgs"]))
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
            out.append(for_query(e))
    out.sort(key=_listing_sort_key)
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
