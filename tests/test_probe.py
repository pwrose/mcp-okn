import mcp_okn.server as srv
from mcp_okn.server import (
    _predicate_to_iri,
    probe_namespaces,
    _namespace_query,
    _crosswalk_query,
    find_crosswalks,
    _undercount_note,
    _CROSSWALK_PREDICATES,
)


def test_undercount_note_fires_only_for_multiple_namespaces():
    assert _undercount_note([{"namespace": "MONDO", "count": 10}]) is None
    assert _undercount_note([]) is None
    # Empty-string namespaces don't count toward the multi-namespace trigger.
    assert _undercount_note(
        [{"namespace": "MONDO", "count": 10}, {"namespace": "", "count": 3}]
    ) is None
    note = _undercount_note(
        [{"namespace": "OMIM", "count": 14936}, {"namespace": "MONDO", "count": 7811}]
    )
    assert note is not None
    assert "UNDERCOUNTS" in note and "OMIM" in note and "MONDO" in note


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
    # Two namespaces (MONDO, DOID) -> undercount note is surfaced.
    assert out["note"] is not None and "UNDERCOUNTS" in out["note"]


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
    assert "find_crosswalks" in out["next_step"]


def test_crosswalk_query_covers_mapping_predicates():
    q = _crosswalk_query("NG")
    # Every standard mapping predicate is offered via VALUES, grouped per pred.
    for iri in _CROSSWALK_PREDICATES.values():
        assert f"<{iri}>" in q
    # The OBO db-xref bridge (OMIM/UMLS/MESH -> MONDO in ubergraph) is included.
    assert "oboInOwl:hasDbXref" in _CROSSWALK_PREDICATES
    assert "<http://www.geneontology.org/formats/oboInOwl#hasDbXref>" in q
    assert "VALUES ?pred" in q
    assert "GROUP BY ?pred ?namespace" in q
    assert "LIMIT" not in q
    # Sampling caps via an inner subquery over (pred, o).
    qs = _crosswalk_query("NG", sample=10000)
    assert "LIMIT 10000" in qs
    assert "SELECT ?pred ?o WHERE" in qs


async def test_find_crosswalks_groups_by_predicate(monkeypatch):
    see_also = _CROSSWALK_PREDICATES["rdfs:seeAlso"]
    exact = _CROSSWALK_PREDICATES["skos:exactMatch"]

    async def fake_run(query, fmt="json", **kw):
        return {
            "vars": ["pred", "namespace", "count"],
            "rows": [
                {"pred": see_also, "namespace": "PubChem", "count": 100},
                {"pred": exact, "namespace": "MONDO", "count": 250},
                {"pred": see_also, "namespace": "UniProt", "count": 40},
            ],
            "row_count": 3,
        }

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await find_crosswalks("prokn")

    # Busiest predicate first; CURIE label resolved; namespaces sorted desc.
    assert [c["predicate"] for c in out["crosswalks"]] == ["skos:exactMatch", "rdfs:seeAlso"]
    see = next(c for c in out["crosswalks"] if c["predicate"] == "rdfs:seeAlso")
    assert see["predicate_iri"] == see_also
    assert see["total"] == 140
    assert see["namespaces"][0] == {"namespace": "PubChem", "count": 100}
    assert out["sampled"] is None
