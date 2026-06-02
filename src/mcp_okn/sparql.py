"""Client for the FRINK federated SPARQL endpoint.

This is the ONLY network path used to run queries. The per-KG SPARQL endpoints
listed in the registry (Apache Jena instances) are deliberately never used: they
time out or run out of memory on complex queries. Every query is sent to the
QLever-backed federation endpoint and scoped to named graphs.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

#: The single federation endpoint. Do not query per-KG endpoints.
FEDERATION_ENDPOINT = "https://frink.apps.renci.org/federation/sparql"

#: Template for a KG's federation named graph URI.
GRAPH_URI = "https://purl.org/okn/frink/kg/{shortname}"

_ACCEPT = {
    "json": "application/sparql-results+json",
    "csv": "text/csv",
    "tsv": "text/tsv",
}


def named_graph(shortname: str) -> str:
    """Return the federation named-graph URI for a KG shortname."""
    return GRAPH_URI.format(shortname=shortname)


# schema.org's canonical RDF namespace is `http://schema.org/`, which is what the
# Proto-OKN KGs store, but models routinely write the `https://` website form.
# The two are distinct IRIs to a SPARQL engine, so an `https://schema.org/...`
# term silently matches nothing. Normalize it to the `http://` form so queries
# work regardless of which scheme the author used.
_SCHEMA_ORG_HTTPS = re.compile(r"https://schema\.org/")


def normalize_schema_org(query: str) -> str:
    """Rewrite ``https://schema.org/`` → ``http://schema.org/`` in a query."""
    return _SCHEMA_ORG_HTTPS.sub("http://schema.org/", query)


class SparqlError(RuntimeError):
    """Raised when the endpoint returns an error for a query."""


def _flatten_bindings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn SPARQL JSON results bindings into compact {var: value} rows."""
    rows: list[dict[str, Any]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        row: dict[str, Any] = {}
        for var, cell in binding.items():
            value = cell.get("value")
            # Cast common numeric/boolean datatypes for convenience.
            dtype = cell.get("datatype", "")
            if dtype.endswith(("integer", "int", "long", "decimal", "double", "float")):
                try:
                    value = float(value) if "." in value or "e" in value.lower() else int(value)
                except (TypeError, ValueError):
                    pass
            elif dtype.endswith("boolean"):
                value = value == "true"
            row[var] = value
        rows.append(row)
    return rows


async def run_sparql(
    query: str,
    fmt: str = "json",
    timeout: float = 120.0,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Run a SPARQL query against the FRINK federation endpoint.

    Args:
        query: A complete SPARQL query string. Scope to a KG with
            ``GRAPH <https://purl.org/okn/frink/kg/{shortname}> { ... }``.
        fmt: Output format: ``json`` (default, parsed into rows), ``csv`` or
            ``tsv`` (returned as raw text).
        timeout: Request timeout in seconds.
        client: Optional shared httpx.AsyncClient.

    Returns:
        For ``json``: ``{"vars": [...], "rows": [...], "row_count": N}``.
        For ``csv``/``tsv``: ``{"format": fmt, "text": "..."}``.

    Raises:
        SparqlError: If the endpoint reports a query error (including the
            read-only-filesystem error QLever raises for large external sorts).
    """
    if fmt not in _ACCEPT:
        raise ValueError(f"Unsupported format {fmt!r}; use one of {sorted(_ACCEPT)}")

    query = normalize_schema_org(query)
    headers = {"Accept": _ACCEPT[fmt]}
    data = {"query": query}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        resp = await client.post(
            FEDERATION_ENDPOINT, data=data, headers=headers, timeout=timeout
        )
    finally:
        if owns_client:
            await client.aclose()

    text = resp.text

    # QLever returns HTTP 400 with a JSON body containing an "exception" field
    # (e.g. the "Read-only file system" sort error) on query failure.
    if resp.status_code != 200:
        message = text
        try:
            err = json.loads(text)
            message = err.get("exception", text)
        except (json.JSONDecodeError, ValueError):
            pass
        raise SparqlError(
            f"SPARQL endpoint returned HTTP {resp.status_code}: "
            f"{message.strip()}\nQuery:\n{query}"
        )

    if fmt != "json":
        return {"format": fmt, "text": text}

    payload = resp.json()
    rows = _flatten_bindings(payload)
    return {
        "vars": payload.get("head", {}).get("vars", []),
        "rows": rows,
        "row_count": len(rows),
    }
