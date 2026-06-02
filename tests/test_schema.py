import csv
from io import StringIO

from mcp_okn.schema import (
    _build_schema_from_metadata,
    _generate_query_template,
    _should_exclude_uri,
)


def _parse(csv_text: str) -> dict[str, dict[str, str]]:
    """Mirror fetch_entity_metadata's CSV parsing (without the network)."""
    metadata: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(StringIO(csv_text)):
        uri = (row.get("URI") or "").strip()
        if not uri:
            continue
        edge_property_of = (row.get("EdgePropertyOf") or "").strip()
        if uri in metadata and edge_property_of:
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
    return metadata


SIMPLE_CSV = """\
URI,Label,Description,Type
http://schema.org/Person,Person,A human being.,Class
http://schema.org/name,name,The name of the thing.,Predicate
"""

EDGE_CSV = """\
URI,Label,Description,Type,EdgePropertyOf,SourceClass,TargetClass
https://ex.org/schema/Gene,Gene,A gene.,Class,,,
https://ex.org/schema/Sample,Sample,A sample.,Class,,,
https://ex.org/schema/MEASURED_EXPR,MEASURED_EXPR,Expression edge.,Predicate,,Sample,Gene
https://ex.org/schema/log2fc,log2fc,Log2 fold change (float).,EdgeProperty,MEASURED_EXPR,,
https://ex.org/schema/pval,pval,P-value (float).,EdgeProperty,MEASURED_EXPR,,
"""


def test_build_schema_classes_and_predicates():
    schema = _build_schema_from_metadata("demo", _parse(SIMPLE_CSV), compact=True)
    assert schema["classes"]["count"] == 1
    assert schema["classes"]["data"][0][0] == "http://schema.org/Person"
    assert schema["predicates"]["count"] == 1
    # No edge properties -> the predicate is not flagged.
    pred_row = schema["predicates"]["data"][0]
    has_edge_props = pred_row[schema["predicates"]["columns"].index("has_edge_properties")]
    assert has_edge_props is False
    assert schema["edge_properties"] == {}
    # Compact omits the summary.
    assert "edge_property_summary" not in schema


def test_build_schema_edge_properties_and_template():
    schema = _build_schema_from_metadata("demo", _parse(EDGE_CSV), compact=False)
    edges = schema["edge_properties"]
    assert "MEASURED_EXPR" in edges
    edge = edges["MEASURED_EXPR"]
    assert edge["source_class"] == "Sample"
    assert edge["target_class"] == "Gene"
    assert {p["label"] for p in edge["properties"]} == {"log2fc", "pval"}
    # The predicate carrying edge properties is flagged.
    pred_row = next(
        r for r in schema["predicates"]["data"] if r[0].endswith("MEASURED_EXPR")
    )
    idx = schema["predicates"]["columns"].index("has_edge_properties")
    assert pred_row[idx] is True
    # Non-compact prepends the edge-property summary.
    assert "edge_property_summary" in schema
    # The generated template uses RDF reification scoped to the KG schema NS.
    tmpl = edge["query_template"]
    assert "rdf:subject" in tmpl
    assert "schema:MEASURED_EXPR" in tmpl
    assert "purl.org/okn/frink/kg/demo/schema/" in tmpl


def test_edge_property_of_accumulates_multiple_parents():
    csv_text = """\
URI,Label,Description,Type,EdgePropertyOf,SourceClass,TargetClass
https://ex.org/schema/adj_p,adj_p,Adjusted p-value.,EdgeProperty,EXPRESSION,,
https://ex.org/schema/adj_p,adj_p,Adjusted p-value.,EdgeProperty,ABUNDANCE,,
"""
    meta = _parse(csv_text)
    assert meta["https://ex.org/schema/adj_p"]["edge_property_of"] == "EXPRESSION;ABUNDANCE"


def test_generate_query_template_shape():
    props = [{"label": "score"}]
    tmpl = _generate_query_template("demo", "RELATES", "Foo", "Bar", props)
    assert "SELECT ?foo ?bar ?score" in tmpl
    assert "schema:RELATES" in tmpl
    assert tmpl.rstrip().endswith("}")


def test_should_exclude_rdf_syntax_uris():
    assert _should_exclude_uri("http://www.w3.org/1999/02/22-rdf-syntax-ns#_1")
    assert not _should_exclude_uri("http://schema.org/Person")
