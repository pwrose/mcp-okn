"""Schema discovery for Proto-OKN knowledge graphs.

For each KG we prefer a curated entity metadata CSV (classes, predicates, edge
properties, node properties) published in the ``sbl-sdsc/mcp-proto-okn`` repo.
When no curated metadata exists we fall back to probing the federation endpoint
for the distinct classes and predicates used in the KG's named graph.

The shortnames are the same ones used as federation named graphs
(``https://purl.org/okn/frink/kg/{shortname}``), so the curated CSVs port over
directly from the proto-okn server.
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any

import httpx

from .sparql import named_graph, run_sparql

#: Where the curated per-KG entity metadata CSVs live.
ENTITY_METADATA_BASE = (
    "https://raw.githubusercontent.com/sbl-sdsc/mcp-proto-okn/"
    "main/metadata/entities"
)

#: Schema namespace template used inside generated edge-property templates.
_SCHEMA_NS = "https://purl.org/okn/frink/kg/{shortname}/schema/"

#: KGs too large to enumerate a schema for via brute-force SPARQL probing.
_TOO_LARGE = {"ubergraph"}

# Process-lifetime cache of parsed entity metadata, keyed by shortname.
_metadata_cache: dict[str, dict[str, dict[str, str]]] = {}


async def fetch_entity_metadata(
    shortname: str,
    client: httpx.AsyncClient | None = None,
    refresh: bool = False,
) -> dict[str, dict[str, str]]:
    """Fetch and parse the curated entity metadata CSV for a KG (cached).

    Returns a dict mapping each URI to ``{label, description, type,
    edge_property_of, source_class, target_class}``. Returns an empty dict when
    no curated CSV exists for the KG (the caller then falls back to probing).
    """
    if shortname in _metadata_cache and not refresh:
        return _metadata_cache[shortname]

    url = f"{ENTITY_METADATA_BASE}/{shortname}_entities.csv"
    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            _metadata_cache[shortname] = {}
            return {}
        content = resp.text
    except httpx.HTTPError:
        _metadata_cache[shortname] = {}
        return {}
    finally:
        if owns:
            await client.aclose()

    metadata: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(StringIO(content)):
        uri = (row.get("URI") or "").strip()
        if not uri:
            continue
        edge_property_of = (row.get("EdgePropertyOf") or "").strip()
        if uri in metadata and edge_property_of:
            # A single edge-property URI can belong to several relationships
            # (e.g. adj_p_value on both EXPRESSION and ABUNDANCE). Accumulate
            # the parents semicolon-separated so the join below finds them all.
            existing = metadata[uri].get("edge_property_of", "")
            metadata[uri]["edge_property_of"] = (
                f"{existing};{edge_property_of}" if existing else edge_property_of
            )
        else:
            metadata[uri] = {
                "label": (row.get("Label") or "").strip(),
                "description": (row.get("Description") or "").strip(),
                "type": (row.get("Type") or "").strip(),
                "edge_property_of": edge_property_of,
                "source_class": (row.get("SourceClass") or "").strip(),
                "target_class": (row.get("TargetClass") or "").strip(),
            }

    _metadata_cache[shortname] = metadata
    return metadata


def _generate_query_template(
    shortname: str,
    relationship_label: str,
    source_class: str,
    target_class: str,
    properties: list[dict[str, Any]],
) -> str:
    """Generate a SPARQL template for a reified relationship with edge properties."""
    source_var = source_class.lower() if source_class else "source"
    target_var = target_class.lower() if target_class else "target"
    schema_ns = _SCHEMA_NS.format(shortname=shortname)

    prop_selects = [f"?{p['label']}" for p in properties]
    prop_patterns = [f"        schema:{p['label']} ?{p['label']} ;" for p in properties]
    if prop_patterns:
        prop_patterns[-1] = prop_patterns[-1].rstrip(" ;") + " ."

    return (
        "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
        f"PREFIX schema: <{schema_ns}>\n\n"
        f"SELECT ?{source_var} ?{target_var} {' '.join(prop_selects)}\n"
        "WHERE {\n"
        f"  ?stmt rdf:subject ?{source_var} ;\n"
        f"        rdf:predicate schema:{relationship_label} ;\n"
        f"        rdf:object ?{target_var} ;\n"
        f"{chr(10).join(prop_patterns)}\n"
        "}"
    )


def _build_schema_from_metadata(
    shortname: str, entity_metadata: dict[str, dict[str, str]], compact: bool
) -> dict[str, Any]:
    """Build the schema response from curated entity metadata."""
    classes: list[dict[str, str]] = []
    predicates: list[dict[str, Any]] = []
    edge_properties_dict: dict[str, list[dict[str, str]]] = {}
    node_properties: list[dict[str, str]] = []

    for uri, meta in entity_metadata.items():
        entity_type = (meta.get("type") or "").lower()
        if entity_type == "class":
            classes.append(
                {
                    "uri": uri,
                    "label": meta.get("label", ""),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", ""),
                }
            )
        elif entity_type == "predicate":
            short_name = uri.split("/")[-1] if "/" in uri else uri
            predicates.append(
                {
                    "uri": uri,
                    "short_name": short_name,
                    "label": meta.get("label", ""),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", ""),
                    "source_class": meta.get("source_class", ""),
                    "target_class": meta.get("target_class", ""),
                    "has_edge_properties": False,
                }
            )
        elif entity_type == "edgeproperty":
            parents = meta.get("edge_property_of", "")
            for parent in (p.strip() for p in parents.split(";") if p.strip()):
                edge_properties_dict.setdefault(parent, []).append(
                    {
                        "uri": uri,
                        "label": meta.get("label", ""),
                        "description": meta.get("description", ""),
                        "type": meta.get("type", ""),
                    }
                )
        elif entity_type == "nodeproperty":
            node_properties.append(
                {
                    "uri": uri,
                    "label": meta.get("label", ""),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", ""),
                    "class": meta.get("source_class", ""),
                }
            )

    # Flag predicates that carry edge properties (match on short name).
    for pred in predicates:
        if pred["short_name"] in edge_properties_dict:
            pred["has_edge_properties"] = True

    edge_properties_output: dict[str, Any] = {}
    for relationship_label, props in edge_properties_dict.items():
        rel = next(
            (p for p in predicates if p["short_name"] == relationship_label), None
        )
        if rel is None:
            continue
        edge_properties_output[relationship_label] = {
            "uri": rel["uri"],
            "label": relationship_label,
            "description": rel["description"],
            "source_class": rel["source_class"],
            "target_class": rel["target_class"],
            "properties": props,
            "query_template": _generate_query_template(
                shortname,
                relationship_label,
                rel["source_class"],
                rel["target_class"],
                props,
            ),
        }

    result: dict[str, Any] = {
        "classes": {
            "columns": ["uri", "label", "description", "type"],
            "data": [
                [c["uri"], c["label"], c["description"], c["type"]] for c in classes
            ],
            "count": len(classes),
        },
        "predicates": {
            "columns": [
                "uri",
                "label",
                "description",
                "type",
                "source_class",
                "target_class",
                "has_edge_properties",
            ],
            "data": [
                [
                    p["uri"],
                    p["label"],
                    p["description"],
                    p["type"],
                    p["source_class"],
                    p["target_class"],
                    p["has_edge_properties"],
                ]
                for p in predicates
            ],
            "count": len(predicates),
        },
        "edge_properties": edge_properties_output,
        "node_properties": {
            "columns": ["uri", "label", "description", "type", "class"],
            "data": [
                [n["uri"], n["label"], n["description"], n["type"], n["class"]]
                for n in node_properties
            ],
            "count": len(node_properties),
        },
    }

    if not compact and edge_properties_output:
        result = {
            "edge_property_summary": {
                "CRITICAL_NOTE": (
                    "Some relationships have edge properties (data stored on the "
                    "relationship itself). To query these, use the RDF reification "
                    "pattern shown in each edge's query_template."
                ),
                "edges_with_properties": [
                    {
                        "relationship": label,
                        "uri": info["uri"],
                        "properties": [
                            {
                                "name": p.get("label", ""),
                                "type": p.get("description", "")
                                .split("(")[-1]
                                .rstrip(")"),
                            }
                            for p in info.get("properties", [])
                        ],
                        "example_query": info.get("query_template", ""),
                    }
                    for label, info in edge_properties_output.items()
                ],
            },
            **result,
        }

    return result


def _should_exclude_uri(uri: str) -> bool:
    """Filter out RDF-syntax-namespace URIs (e.g. container props rdf:_1, rdf:_2)."""
    return uri.startswith(
        (
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "https://www.w3.org/1999/02/22-rdf-syntax-ns#",
        )
    )


async def _probe_schema(shortname: str) -> dict[str, Any]:
    """Discover classes and predicates by probing the federation endpoint.

    Used when no curated entity metadata exists for the KG. Scopes each query to
    the KG's named graph via a ``GRAPH`` block.
    """
    graph = named_graph(shortname)
    class_query = f"""\
SELECT DISTINCT ?class WHERE {{
  GRAPH <{graph}> {{
    {{ ?s a ?class . }}
    UNION {{ ?s <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?class . }}
    UNION {{ ?class a <http://www.w3.org/2000/01/rdf-schema#Class> . }}
    UNION {{ ?class a <http://www.w3.org/2002/07/owl#Class> . }}
  }}
}} ORDER BY ?class"""

    predicate_query = f"""\
SELECT DISTINCT ?predicate WHERE {{
  GRAPH <{graph}> {{
    ?s ?predicate ?o .
  }}
}} ORDER BY ?predicate"""

    classes = await run_sparql(class_query)
    predicates = await run_sparql(predicate_query)

    class_data = [
        [r["class"]]
        for r in classes.get("rows", [])
        if r.get("class") and not _should_exclude_uri(r["class"])
    ]
    predicate_data = [
        [r["predicate"]]
        for r in predicates.get("rows", [])
        if r.get("predicate") and not _should_exclude_uri(r["predicate"])
    ]

    return {
        "classes": {"columns": ["uri"], "data": class_data, "count": len(class_data)},
        "predicates": {
            "columns": ["uri"],
            "data": predicate_data,
            "count": len(predicate_data),
        },
        "edge_properties": {},
        "node_properties": {"columns": ["uri"], "data": [], "count": 0},
    }


async def get_schema(shortname: str, compact: bool = True) -> dict[str, Any]:
    """Return the schema (classes, predicates, edge/node properties) for a KG.

    Prefers curated entity metadata; falls back to probing the federation
    endpoint for distinct classes and predicates.

    Args:
        shortname: The KG shortname (e.g. ``prokn``, ``spoke``), as returned by
            ``list_kgs``.
        compact: If True (default), omit the prepended ``edge_property_summary``
            section. Set False for the richer summary.
    """
    if shortname in _TOO_LARGE:
        return {
            "shortname": shortname,
            "error": (
                f"`{shortname}` is too large to enumerate a schema for; query it "
                "directly with known ontology terms instead."
            ),
        }

    entity_metadata = await fetch_entity_metadata(shortname)
    if entity_metadata:
        schema = _build_schema_from_metadata(shortname, entity_metadata, compact)
    else:
        schema = await _probe_schema(shortname)

    return {"shortname": shortname, "schema": schema}
