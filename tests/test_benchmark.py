"""Offline tests for the benchmark's pure logic: parsing, adaptation, scoring.

These never hit the network and always run. The live smoke layer is exercised
separately by ``test_benchmark_smoke.py`` (opt-in).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import adapt, dataset, score  # noqa: E402

SERVED = {"hydrologykg", "ruralkg", "prokn", "ubergraph", "fiokg", "nde"}


# --- parsing ---------------------------------------------------------------

def test_parse_rq_extracts_header_and_body():
    text = (
        "#+ summary: Retrieve all flowlines named 'Illinois River'\n"
        "#+ tags:\n"
        "#+   - hydrologykg\n"
        "\n"
        "SELECT * WHERE { ?s ?p ?o }\n"
    )
    summary, tags, body = adapt.parse_rq(text)
    assert summary == "Retrieve all flowlines named 'Illinois River'"
    assert tags == ["hydrologykg"]
    assert body == "SELECT * WHERE { ?s ?p ?o }"
    assert "#+" not in body


def test_parse_rq_handles_missing_header():
    summary, tags, body = adapt.parse_rq("SELECT * { ?s ?p ?o }")
    assert summary == "" and tags == []
    assert body == "SELECT * { ?s ?p ?o }"


# --- GRAPH wrapping --------------------------------------------------------

def test_graph_wrap_scopes_where_keeps_modifiers_outside():
    q = "SELECT * WHERE {\n  ?s ?p ?o .\n  FILTER(?o > 1)\n} ORDER BY ?s"
    out = adapt.graph_wrap(q, "hydrologykg")
    assert "GRAPH <https://purl.org/okn/frink/kg/hydrologykg> {" in out
    # ORDER BY must remain outside the GRAPH block (after the closing brace).
    assert out.rstrip().endswith("ORDER BY ?s")
    assert out.index("FILTER") < out.index("ORDER BY")


def test_graph_wrap_ignores_braces_in_comments_and_strings():
    q = 'SELECT * WHERE {\n  # a stray } brace in a comment\n  ?s ?p "a } string" .\n}'
    out = adapt.graph_wrap(q, "prokn")
    # The wrap must close at the real end, leaving exactly one GRAPH block.
    assert out.count("GRAPH <") == 1
    assert out.strip().endswith("}")


# --- keyword detection (the false-positive that bit us) --------------------

def test_variable_named_service_is_not_the_service_keyword():
    q = "SELECT ?service WHERE { ?x :hasService ?service }"
    pq = adapt.adapt("s", ["ruralkg"], q, SERVED)
    assert pq.adaptation == "auto"
    assert pq.federated is not None


def test_real_service_clause_is_flagged_manual():
    q = "SELECT * WHERE { SERVICE <http://x/sparql> { ?s ?p ?o } }"
    pq = adapt.adapt("s", ["prokn"], q, SERVED)
    assert pq.adaptation == "manual"
    assert "GRAPH/SERVICE" in pq.adaptation_note


def test_qlever_unsupported_function_is_incompatible():
    q = (
        "PREFIX ofn: <http://www.ontotext.com/sparql/functions/>\n"
        "SELECT * WHERE { ?s :dur ?d . BIND(ofn:asDays(?d) AS ?days) }"
    )
    pq = adapt.adapt("s", ["sockg"], q, SERVED | {"sockg"})
    assert pq.adaptation == "incompatible"
    assert pq.federated is None
    assert "QLever" in pq.adaptation_note


# --- tag mapping / scoping -------------------------------------------------

def test_multi_kg_is_manual():
    pq = adapt.adapt("s", ["evoweb", "ubergraph"], "SELECT * {?s ?p ?o}", SERVED | {"evoweb"})
    assert pq.adaptation == "manual"
    assert "multi-KG" in pq.adaptation_note


def test_unmapped_tag_is_skipped():
    pq = adapt.adapt("s", ["federation"], "SELECT * {?s ?p ?o}", SERVED)
    assert pq.adaptation == "skip"


def test_tag_map_override_applies():
    pq = adapt.adapt("s", ["fio-kg"], "SELECT * {?s ?p ?o}", SERVED, {"fio-kg": "fiokg"})
    assert pq.adaptation == "auto"
    assert pq.extra["mapped_kgs"] == ["fiokg"]


# --- scoring ---------------------------------------------------------------

def test_score_exact_ignores_column_names_and_order():
    ref = [{"disease": "A", "label": "x"}, {"disease": "B", "label": "y"}]
    # Same denotation, renamed columns, reordered rows and columns.
    cand = [{"l": "y", "d": "B"}, {"l": "x", "d": "A"}]
    cmp = score.compare(ref, cand)
    assert cmp.exact is True and cmp.f1 == 1.0


def test_score_partial_overlap():
    ref = [{"x": "1"}, {"x": "2"}, {"x": "3"}, {"x": "4"}]
    cand = [{"x": "1"}, {"x": "2"}]
    cmp = score.compare(ref, cand)
    assert cmp.exact is False
    assert cmp.recall == 0.5 and cmp.precision == 1.0


def test_score_numeric_string_equivalence():
    cmp = score.compare([{"n": 1}], [{"n": "1"}])
    assert cmp.exact is True


def test_score_both_empty_is_exact():
    assert score.compare([], []).exact is True


# --- dataset integrity (runs against the committed dataset.jsonl) -----------

def test_committed_dataset_is_well_formed():
    records = dataset.load()
    assert records, "dataset.jsonl is empty — run fetch_registry"
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)), "duplicate ids in dataset"
    for r in records:
        assert r["adaptation"] in {"auto", "manual", "incompatible", "skip"}
        if r["adaptation"] == "auto":
            assert r["federated"], f"{r['id']} is auto but has no federated query"
            assert "GRAPH <https://purl.org/okn/frink/kg/" in r["federated"]
            assert len(r["mapped_kgs"]) == 1
