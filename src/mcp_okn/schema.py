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
import re
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

#: Mermaid `style` declaration applied to edge (relationship) classes so they
#: read differently from node classes. The per-class `style` statement is the
#: form that actually renders fills in `classDiagram` (a `classDef` + `:::`
#: assignment parses but emits no fill in current Mermaid).
_EDGE_CLASS_STYLE = "fill:#FFE0B2,stroke:#E65100,color:#000"

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


# ── Mermaid class-diagram generation ─────────────────────────────────────────


def _local_name(uri: str) -> str:
    """Return the last path/fragment segment of a URI."""
    return re.split(r"[/#]", uri.rstrip("/#"))[-1] if uri else uri


def _mermaid_id(name: str) -> str:
    """Sanitize a label or URI into a Mermaid-safe class identifier."""
    if name.startswith(("http://", "https://")):
        name = _local_name(name)
    ident = re.sub(r"\W+", "_", name or "").strip("_")
    return ident or "Node"


def _member_type(description: str) -> str:
    """Extract a field type from a trailing ``(type)`` in a property description.

    Returns "" when the description has no such marker — we deliberately do NOT
    fall back to the entity ``type`` (which is always "EdgeProperty"/
    "NodeProperty" and useless as a data type).
    """
    m = re.search(r"\(([^()]+)\)[.\s]*$", (description or "").strip())
    if m:
        candidate = m.group(1).strip()
        if candidate and " " not in candidate and len(candidate) <= 20:
            return candidate.lower()
    return ""


def _clean_edge_label(label: str) -> str:
    """Strip characters that would break a Mermaid relationship label."""
    return re.sub(r"\s+", " ", (label or "").replace("|", " ").replace("\n", " ")).strip()


def build_mermaid_diagram(shortname: str, schema: dict[str, Any]) -> str:
    """Render a KG's schema as a Mermaid ``classDiagram`` (deterministic).

    Node classes become class boxes (with node properties as members), edge
    predicates with source/target metadata become labeled arrows, and predicates
    carrying edge properties become intermediary classes with typed fields
    wired ``source --> edge --> target``. Predicates lacking source/target
    metadata are listed as ``%%`` comments rather than guessed at.
    """
    classes_tbl = schema.get("classes", {})
    predicates_tbl = schema.get("predicates", {})
    node_props_tbl = schema.get("node_properties", {})
    edge_properties = schema.get("edge_properties", {}) or {}

    declared: dict[str, list[str]] = {}  # class id -> member lines (insertion order)
    relationships: list[str] = []
    undrawn: list[str] = []
    edge_class_ids: list[str] = []  # intermediary classes (styled distinctly)

    def ensure_class(label: str) -> str:
        cid = _mermaid_id(label)
        declared.setdefault(cid, [])
        return cid

    # Node classes (column layout: [uri, label, ...] for metadata, [uri] for probe).
    cls_cols = classes_tbl.get("columns", [])
    label_idx = cls_cols.index("label") if "label" in cls_cols else None
    for row in classes_tbl.get("data", []):
        if not row:
            continue
        label = row[label_idx] if label_idx is not None and len(row) > label_idx else ""
        ensure_class(label or _local_name(row[0]))

    # Node properties become members of their owning class.
    np_cols = node_props_tbl.get("columns", [])
    np_label = np_cols.index("label") if "label" in np_cols else None
    np_desc = np_cols.index("description") if "description" in np_cols else None
    np_class = np_cols.index("class") if "class" in np_cols else None
    for row in node_props_tbl.get("data", []):
        if not row or np_label is None or np_class is None:
            continue
        owner = row[np_class] if len(row) > np_class else ""
        name = row[np_label] if len(row) > np_label else ""
        if not owner or not name:
            continue
        cid = ensure_class(owner)
        desc = row[np_desc] if np_desc is not None and len(row) > np_desc else ""
        member = f"{_member_type(desc)} {_mermaid_id(name)}".strip()
        if member not in declared[cid]:
            declared[cid].append(member)

    # Edge predicates with properties → intermediary classes.
    for rel_label, info in edge_properties.items():
        edge_id = _mermaid_id(rel_label)
        members = []
        for prop in info.get("properties", []):
            mtype = _member_type(prop.get("description", ""))
            member = f"{mtype} {_mermaid_id(prop.get('label', ''))}".strip()
            if member and member not in members:
                members.append(member)
        declared[edge_id] = members
        if edge_id not in edge_class_ids:
            edge_class_ids.append(edge_id)
        src, tgt = info.get("source_class", ""), info.get("target_class", "")
        if src:
            relationships.append(f"  {ensure_class(src)} --> {edge_id}")
        if tgt:
            relationships.append(f"  {edge_id} --> {ensure_class(tgt)}")

    # Plain predicates (no edge properties) with source/target → labeled arrows.
    pred_cols = predicates_tbl.get("columns", [])
    p_label = pred_cols.index("label") if "label" in pred_cols else None
    p_src = pred_cols.index("source_class") if "source_class" in pred_cols else None
    p_tgt = pred_cols.index("target_class") if "target_class" in pred_cols else None
    p_has = (
        pred_cols.index("has_edge_properties")
        if "has_edge_properties" in pred_cols
        else None
    )
    for row in predicates_tbl.get("data", []):
        if not row:
            continue
        if p_has is not None and len(row) > p_has and row[p_has]:
            continue  # already drawn as an intermediary class
        label = row[p_label] if p_label is not None and len(row) > p_label else ""
        label = label or _local_name(row[0])
        src = row[p_src] if p_src is not None and len(row) > p_src else ""
        tgt = row[p_tgt] if p_tgt is not None and len(row) > p_tgt else ""
        if src and tgt:
            relationships.append(
                f"  {ensure_class(src)} --> {ensure_class(tgt)} : {_clean_edge_label(label)}"
            )
        else:
            undrawn.append(_clean_edge_label(label))

    lines = ["classDiagram", "  direction TB"]
    for cid, members in declared.items():
        if members:
            lines.append(f"  class {cid} {{")
            lines += [f"    {m}" for m in members]
            lines.append("  }")
        else:
            lines.append(f"  class {cid}")
    lines += relationships
    if edge_class_ids:
        lines.append("  %% Edge (relationship) classes — styled to stand out:")
        lines += [f"  style {cid} {_EDGE_CLASS_STYLE}" for cid in edge_class_ids]
    if undrawn:
        lines.append("  %% Predicates without source/target metadata (not drawn):")
        lines += [f"  %%   - {p}" for p in undrawn]

    return "\n".join(lines)


async def visualize_schema(shortname: str) -> dict[str, Any]:
    """Build a Mermaid ``classDiagram`` of a KG's schema, server-side.

    Returns ``{"shortname", "mermaid"}`` on success, or ``{"shortname",
    "error"}`` when the KG has no enumerable schema (e.g. ``ubergraph``).
    """
    result = await get_schema(shortname, compact=True)
    if "error" in result:
        return {"shortname": shortname, "error": result["error"]}
    diagram = build_mermaid_diagram(shortname, result["schema"])
    return {"shortname": shortname, "mermaid": diagram}
