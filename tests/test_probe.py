import mcp_okn.server as srv
from mcp_okn.server import _predicate_to_iri, probe_namespaces, _namespace_query


def test_predicate_to_iri_resolves_curies_and_iris():
    assert _predicate_to_iri("schema:healthCondition") == "http://schema.org/healthCondition"
    # https schema.org is normalized to the http form the KGs store.
    assert _predicate_to_iri("https://schema.org/about") == "http://schema.org/about"
    assert _predicate_to_iri("rdfs:seeAlso") == "http://www.w3.org/2000/01/rdf-schema#seeAlso"
    assert _predicate_to_iri("MONDO:0005240") == "http://purl.obolibrary.org/obo/MONDO_0005240"
    assert _predicate_to_iri("<http://x.org/p>") == "http://x.org/p"
    # Unknown bare CURIE can't be resolved.
    assert _predicate_to_iri("foo:bar") is None


async def test_probe_namespaces_aggregates_rows(monkeypatch):
    captured = {}

    async def fake_run(query, fmt="json", **kw):
        captured["query"] = query
        return {
            "vars": ["namespace", "count"],
            "rows": [
                {"namespace": "MONDO", "count": 250611},
                {"namespace": "DOID", "count": 20431},
            ],
            "row_count": 2,
        }

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await probe_namespaces("nde", "schema:healthCondition")

    assert out["shortname"] == "nde"
    assert out["predicate"] == "http://schema.org/healthCondition"
    assert out["namespaces"][0] == {"namespace": "MONDO", "count": 250611}
    assert out["total"] == 271042
    # Query is scoped to the KG's named graph and the resolved predicate IRI.
    assert "https://purl.org/okn/frink/kg/nde" in captured["query"]
    assert "http://schema.org/healthCondition" in captured["query"]
    # Default run is an exact full scan.
    assert out["sampled"] is None
    assert "LIMIT" not in captured["query"]


async def test_probe_namespaces_passes_sample_through(monkeypatch):
    captured = {}

    async def fake_run(query, fmt="json", **kw):
        captured["query"] = query
        return {"vars": ["namespace", "count"], "rows": [], "row_count": 0}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await probe_namespaces("nde", "schema:healthCondition", sample=2000)
    assert out["sampled"] == 2000
    assert "LIMIT 2000" in captured["query"]


async def test_probe_namespaces_rejects_unresolvable_predicate(monkeypatch):
    called = False

    async def fake_run(query, fmt="json", **kw):
        nonlocal called
        called = True
        return {"vars": [], "rows": [], "row_count": 0}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await probe_namespaces("nde", "foo:bar")
    assert "error" in out
    assert called is False  # never hits the endpoint


def test_namespace_query_extracts_obo_prefix_logic():
    # Sanity: the built query references the grouping var and alpha-prefix regex.
    q = _namespace_query("NG", "http://p")
    assert "GROUP BY ?namespace" in q
    assert "[_:][A-Za-z0-9]*[0-9]" in q
    assert "LIMIT" not in q  # exact full scan by default


def test_namespace_query_sample_wraps_limit_subquery():
    q = _namespace_query("NG", "http://p", sample=5000)
    assert "LIMIT 5000" in q
    assert "SELECT ?o WHERE" in q  # inner sampling subquery
    # Zero/negative is treated as an exact full scan.
    assert "LIMIT" not in _namespace_query("NG", "http://p", sample=0)
    assert "LIMIT" not in _namespace_query("NG", "http://p", sample=-10)


async def test_get_schema_surfaces_probe_namespaces_hint(monkeypatch):
    async def fake_schema(shortname, compact=True):
        return {"shortname": shortname, "schema": {"predicates": {"count": 1}}}

    monkeypatch.setattr(srv.schema, "get_schema", fake_schema)
    out = await srv.get_schema("nde")
    assert "next_step" in out
    assert "probe_namespaces" in out["next_step"]
