import mcp_okn.server as srv
from mcp_okn.server import (
    _predicate_to_iri,
    probe_namespaces,
    _namespace_query,
    _crosswalk_query,
    _ontology_id_query,
    _NODE_ID_IRI_PREFIXES,
    find_crosswalks,
    _undercount_note,
    _split_predicate_note,
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


def test_split_predicate_note_fires_only_for_multi_predicate_namespace():
    assert _split_predicate_note([]) is None
    # One predicate carrying the namespace — nothing is split.
    assert _split_predicate_note(
        [{"namespace": "MONDO", "predicates": [("biolink:object", 1659)]}]
    ) is None
    note = _split_predicate_note(
        [{"namespace": "MONDO",
          "predicates": [("biolink:subject", 2277), ("biolink:object", 1659)]}]
    )
    assert note is not None
    assert "SPLIT across predicate positions" in note and "UNION" in note
    assert "MONDO" in note and "biolink:subject" in note


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


# Route a fake run_sparql to one of the three scans find_crosswalks fires.
def _scan_of(query: str) -> str:
    if "VALUES ?pred" in query:
        return "crosswalk"
    if "?n ?pred ?x" in query:
        return "subject"
    if "?x ?pred ?n" in query:
        return "object"
    raise AssertionError(f"unrecognized scan query: {query[:80]}")


def test_ontology_id_query_scans_one_role_per_query():
    subj = _ontology_id_query("NG", "subject")
    obj = _ontology_id_query("NG", "object")
    # Each query scans exactly its own role's triple position.
    assert "?n ?pred ?x" in subj and "?x ?pred ?n" not in subj
    assert "?x ?pred ?n" in obj and "?n ?pred ?x" not in obj
    # No UNION: the two roles are separate requests so one can't time out the
    # other; each groups per (pred, ns) and counts distinct join keys.
    assert "UNION" not in subj
    assert "GROUP BY ?pred ?namespace" in subj
    assert "COUNT(DISTINCT ?n)" in subj
    # Only id-bearing IRI namespaces are scanned, pushed down as a prefilter.
    for prefix in _NODE_ID_IRI_PREFIXES:
        assert f'STRSTARTS(STR(?n), "{prefix}")' in subj
    assert "http://purl.obolibrary.org/obo/" in _NODE_ID_IRI_PREFIXES
    assert "LIMIT" not in subj  # exact full scan by default


def test_ontology_id_query_sample_filters_inside_limit():
    # Sampling must cap ALREADY-FILTERED id triples, else a graph whose first N
    # triples are non-id (reified associations) profiles as empty. The id filter
    # therefore lives inside the LIMIT subquery.
    q = _ontology_id_query("NG", "object", sample=5000)
    assert q.count("LIMIT 5000") == 1
    subquery_prefix = q.index("SELECT ?n ?pred WHERE")
    assert "STRSTARTS(STR(?n)" in q[subquery_prefix : subquery_prefix + 400]


async def test_find_crosswalks_groups_by_predicate(monkeypatch):
    see_also = _CROSSWALK_PREDICATES["rdfs:seeAlso"]
    exact = _CROSSWALK_PREDICATES["skos:exactMatch"]

    async def fake_run(query, fmt="json", **kw):
        scan = _scan_of(query)
        if scan == "subject":
            return {  # MONDO under two predicates: collapses to one row, count=max.
                "vars": ["pred", "namespace", "count"],
                "rows": [
                    {"pred": "http://ex/type", "namespace": "MONDO", "count": 5000},
                    {"pred": "http://ex/label", "namespace": "MONDO", "count": 4000},
                ],
                "row_count": 2,
            }
        if scan == "object":
            return {
                "vars": ["pred", "namespace", "count"],
                "rows": [{"pred": "http://ex/hasPhenotype",
                          "namespace": "HP", "count": 12}],
                "row_count": 1,
            }
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
    # Node-IRI / domain-predicate scan surfaced separately, busiest first; the
    # two MONDO rows collapse to one (count=max), predicates listed busiest-first.
    assert [o["namespace"] for o in out["ontology_ids"]] == ["MONDO", "HP"]
    mondo = out["ontology_ids"][0]
    assert mondo["role"] == "subject"
    assert mondo["count"] == 5000
    assert mondo["predicates"] == ["http://ex/type", "http://ex/label"]
    assert out["ontology_ids"][1]["role"] == "object"


async def test_find_crosswalks_warns_when_namespace_split_across_predicates(monkeypatch):
    # The oard-kg case: a disease IRI is the object of BOTH biolink:subject and
    # biolink:object, with differing counts. Joining on one position undercounts,
    # so the note must flag the split and tell the caller to UNION both.
    async def fake_run(query, fmt="json", **kw):
        if _scan_of(query) == "object":
            return {
                "vars": ["pred", "namespace", "count"],
                "rows": [
                    {"pred": "https://w3id.org/biolink/vocab/subject",
                     "namespace": "MONDO", "count": 2277},
                    {"pred": "https://w3id.org/biolink/vocab/object",
                     "namespace": "MONDO", "count": 1659},
                ],
                "row_count": 2,
            }
        return {"vars": ["pred", "namespace", "count"], "rows": [], "row_count": 0}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await find_crosswalks("oard-kg")
    assert "SPLIT across predicate positions" in out["note"]
    assert "UNION" in out["note"]
    # The collapsed entry still lists both carrying predicates (count = max).
    mondo = out["ontology_ids"][0]
    assert mondo["namespace"] == "MONDO" and mondo["count"] == 2277
    assert len(mondo["predicates"]) == 2


async def test_find_crosswalks_no_split_warning_when_counts_match(monkeypatch):
    # Two predicates carrying the SAME count are likely redundant, not a split —
    # don't cry wolf (the warning is for diverging cardinalities).
    async def fake_run(query, fmt="json", **kw):
        if _scan_of(query) == "object":
            return {
                "vars": ["pred", "namespace", "count"],
                "rows": [
                    {"pred": "http://ex/seeAlso", "namespace": "HP", "count": 50},
                    {"pred": "http://ex/sameAs", "namespace": "HP", "count": 50},
                ],
                "row_count": 2,
            }
        return {"vars": ["pred", "namespace", "count"], "rows": [], "row_count": 0}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await find_crosswalks("prokn")
    assert "SPLIT across predicate positions" not in (out["note"] or "")


async def test_find_crosswalks_node_iris_when_no_mapping_predicates(monkeypatch):
    # The rdkg case: no mapping predicates, but diseases ARE obo/MONDO_ IRIs.
    async def fake_run(query, fmt="json", **kw):
        if _scan_of(query) == "subject":
            return {
                "vars": ["pred", "namespace", "count"],
                "rows": [{"pred": "http://ex/type",
                          "namespace": "MONDO", "count": 8000}],
                "row_count": 1,
            }
        return {"vars": ["pred", "namespace", "count"], "rows": [], "row_count": 0}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await find_crosswalks("rdkg")

    assert out["crosswalks"] == []
    assert out["ontology_ids"][0] == {
        "role": "subject",
        "namespace": "MONDO",
        "count": 8000,
        "predicates": ["http://ex/type"],
    }
    # Note must steer toward a DIRECT node-IRI join, not report "empty".
    assert "directly" in out["note"].lower()
    assert "MONDO" in out["note"]


async def test_find_crosswalks_object_role_survives_subject_timeout(monkeypatch):
    # The biobricks-ice case: ids only as OBJECTS (CHEMINF). The fruitless subject
    # scan times out, but the productive object scan must still come through.
    from mcp_okn.server import SparqlError

    async def fake_run(query, fmt="json", **kw):
        scan = _scan_of(query)
        if scan == "subject":
            raise SparqlError("subject scan timed out")
        if scan == "object":
            return {
                "vars": ["pred", "namespace", "count"],
                "rows": [{"pred": "http://ex/has_role",
                          "namespace": "CHEMINF", "count": 311}],
                "row_count": 1,
            }
        return {"vars": ["pred", "namespace", "count"], "rows": [], "row_count": 0}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await find_crosswalks("biobricks-ice", sample=80000)
    # Object-role ids survive the subject scan's timeout.
    assert [o["namespace"] for o in out["ontology_ids"]] == ["CHEMINF"]
    assert out["ontology_ids"][0]["role"] == "object"
    # …but the partial-scan caveat is still raised, with a smaller retry sample.
    assert "INCOMPLETE" in out["note"]
    assert "sample=40000" in out["note"]


async def test_find_crosswalks_degrades_when_node_scans_fail(monkeypatch):
    from mcp_okn.server import SparqlError

    async def fake_run(query, fmt="json", **kw):
        if _scan_of(query) in ("subject", "object"):
            raise SparqlError("node scan timed out")
        return {
            "vars": ["pred", "namespace", "count"],
            "rows": [{"pred": _CROSSWALK_PREDICATES["skos:exactMatch"],
                      "namespace": "MONDO", "count": 9}],
            "row_count": 1,
        }

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    out = await find_crosswalks("prokn", sample=100000)
    # Crosswalks still returned even though both node-IRI scans errored.
    assert out["crosswalks"][0]["namespaces"][0]["namespace"] == "MONDO"
    assert out["ontology_ids"] == []
    assert "error" not in out
    # The note must flag the incomplete scan and suggest a smaller retry sample.
    assert "INCOMPLETE" in out["note"]
    assert "sample=50000" in out["note"]
    assert "sample=50000" in out["note"]


# --- taxon_overlap -----------------------------------------------------------
from mcp_okn.server import taxon_overlap, _taxon_source, TAXON_HUB_KGS  # noqa: E402


async def test_taxon_overlap_composes_both_skeletons():
    out = await taxon_overlap("nde", "sawgraph")
    assert out["kg_a"] == "nde" and out["kg_b"] == "sawgraph"
    ex, cl = out["exact_id_skeleton"], out["clade_membership_skeleton"]
    # exact-id: each side wrapped in its own DISTINCT subquery on a shared var,
    # joined by COUNT(DISTINCT ?t) — no cross-product, no BIND-on-bound-var.
    assert ex.count("SELECT DISTINCT ?t") == 2
    assert "COUNT(DISTINCT ?t)" in ex
    # both KGs' normalizations are present, plus ubergraph clade closure only on clade.
    assert "https://purl.org/okn/frink/kg/nde" in ex
    assert "https://purl.org/okn/frink/kg/sawgraph" in ex
    assert "subClassOf>*" not in ex
    assert "subClassOf>*" in cl and "kg/ubergraph" in cl
    # clade is directional: kg_b under kg_a, counted on ?b.
    assert "COUNT(DISTINCT ?b)" in cl


async def test_taxon_overlap_surfaces_materialized_pair():
    out = await taxon_overlap("spoke-genelab", "spoke-okn")
    ids = [m["id"] for m in out.get("materialized", [])]
    assert "D9-ncbitaxon-spokegenelab-spokeokn-via-ubergraph" in ids
    assert "verified crosswalk already exists" in out["note"]


async def test_taxon_overlap_rejects_non_hub_kg():
    out = await taxon_overlap("prokn", "nde")
    assert out["status"] == "not_in_taxon_hub"
    assert out["missing"] == ["prokn"]
    assert set(out["taxon_hub_kgs"]) == set(TAXON_HUB_KGS)


def test_taxon_source_spokegenelab_unions_both_representations():
    frag = _taxon_source("spoke-genelab", "a")
    assert "UNION" in frag
    assert "schema/taxonomy" in frag  # Gene.taxonomy model-organism ids
    assert "schema/Organism" in frag  # microbiome label resolution
    # microbiome side resolves the label through ubergraph
    assert "kg/ubergraph" in frag


def test_taxon_source_unknown_kg_returns_none():
    assert _taxon_source("oard-kg", "a") is None


def test_taxon_hub_kgs_match_crosswalk_table():
    """Guard against drift: every KG with a NCBITaxon crosswalk to ubergraph must
    be in TAXON_HUB_KGS with a working _taxon_source, and vice versa. If someone
    adds a taxonomy crosswalk without wiring the tool (or removes one), this fails.
    """
    from mcp_okn import crosswalks

    table_kgs = set()
    for row in crosswalks.all_crosswalks(include_examples=False):
        if row["domain"] == "Taxonomy":
            table_kgs.update(k for k in row["kgs"] if k != "ubergraph")

    hub = set(srv.TAXON_HUB_KGS)
    assert hub == table_kgs, (
        f"TAXON_HUB_KGS out of sync with the crosswalk table: "
        f"only-in-hub={hub - table_kgs}, only-in-table={table_kgs - hub}"
    )
    # and every declared hub KG actually yields a source fragment
    assert all(_taxon_source(kg, "a") is not None for kg in srv.TAXON_HUB_KGS)
