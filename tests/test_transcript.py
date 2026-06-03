import pytest

from mcp_okn import session
from mcp_okn.server import (
    create_chat_transcript,
    latest_transcript_resource,
    _rows_to_table,
)
from mcp_okn.sparql import FEDERATION_ENDPOINT


@pytest.fixture(autouse=True)
def clean_log():
    """Each test starts and ends with an empty session log."""
    session.reset()
    yield
    session.reset()


JSON_RESULT = {
    "vars": ["disease", "label"],
    "rows": [
        {"disease": "MONDO:0005240", "label": "kidney cancer"},
        {"disease": "MONDO:0005089", "label": "testicular cancer"},
    ],
    "row_count": 2,
}


def test_session_records_query_and_detects_graphs():
    logged = session.record(
        "SELECT * WHERE { GRAPH <https://purl.org/okn/frink/kg/sawgraph> { ?s ?p ?o } }",
        "json",
        result=JSON_RESULT,
    )
    assert logged is True
    [entry] = session.entries()
    assert entry["graphs"] == ["sawgraph"]
    assert entry["row_count"] == 2
    assert entry["results"]["rows"][0]["label"] == "kidney cancer"


def test_session_skips_errored_queries():
    assert session.record("BAD QUERY", "json", error="boom") is False
    assert session.entries() == []


def test_session_skips_empty_json_results():
    empty = {"vars": ["x"], "rows": [], "row_count": 0}
    assert session.record("SELECT ?x {}", "json", result=empty) is False
    assert session.entries() == []


def test_session_skips_header_only_csv():
    header_only = {"format": "csv", "text": "x\n"}
    assert session.record("SELECT ?x {}", "csv", result=header_only) is False
    csv_with_rows = {"format": "csv", "text": "x\n1\n2\n"}
    assert session.record("SELECT ?x {}", "csv", result=csv_with_rows) is True
    [entry] = session.entries()
    assert entry["row_count"] == 2


async def test_transcript_renders_logged_queries_as_ground_truth():
    query = (
        "SELECT ?disease ?label WHERE {\n"
        "  GRAPH <https://purl.org/okn/frink/kg/sawgraph> { ?x :linkedTo ?disease }\n"
        "}"
    )
    session.record(query, "json", result=JSON_RESULT)

    md = await create_chat_transcript(
        model="claude-opus-4-8",
        exchanges=[
            {
                "prompt": "Which diseases relate to PFAS?",
                "answer": "Two cancers are associated.",
            }
        ],
        date="2026-05-31",
    )
    # provenance
    assert "**Date:** 2026-05-31" in md
    assert FEDERATION_ENDPOINT in md
    # KGs inferred from the log, not supplied by the caller
    assert "`sawgraph` — <https://purl.org/okn/frink/kg/sawgraph>" in md
    # conversation — mcp-proto-okn style (👤 User / 🧠 Assistant)
    assert "👤 **User**" in md
    assert "Which diseases relate to PFAS?" in md
    assert "🧠 **Assistant**" in md
    assert "Two cancers are associated." in md
    # ground-truth query section with the verbatim query and a results table
    assert "## SPARQL queries executed" in md
    assert "GRAPH <https://purl.org/okn/frink/kg/sawgraph>" in md
    assert "| disease | label |" in md
    assert "| MONDO:0005240 | kidney cancer |" in md


async def test_sparql_query_does_not_log_exploratory(monkeypatch):
    import mcp_okn.server as srv

    async def fake_run(query, fmt="json", **kw):
        return {"vars": ["x"], "rows": [{"x": 1}], "row_count": 1}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    await srv.sparql_query("SELECT ?x {}", exploratory=True)
    assert session.entries() == []
    await srv.sparql_query("SELECT ?x {}")
    assert len(session.entries()) == 1


async def test_sparql_query_does_not_log_empty_result(monkeypatch):
    import mcp_okn.server as srv

    async def fake_run(query, fmt="json", **kw):
        return {"vars": ["x"], "rows": [], "row_count": 0}

    monkeypatch.setattr(srv, "run_sparql", fake_run)
    await srv.sparql_query("SELECT ?x {}")
    assert session.entries() == []


async def test_include_query_log_false_omits_section():
    session.record(
        "SELECT * WHERE { GRAPH <https://purl.org/okn/frink/kg/prokn> {} }",
        "json",
        result={"vars": [], "rows": [], "row_count": 0},
    )
    md = await create_chat_transcript(
        model="m",
        exchanges=[{"prompt": "hi", "answer": "hello"}],
        include_query_log=False,
    )
    assert "## SPARQL queries executed" not in md
    assert "👤 **User**" in md and "hi" in md


async def test_explicit_kgs_override_inference():
    session.record(
        "SELECT * WHERE { GRAPH <https://purl.org/okn/frink/kg/sawgraph> {} }",
        "json",
        result={"vars": [], "rows": [], "row_count": 0},
    )
    md = await create_chat_transcript(model="m", kgs_used=["prokn"])
    assert "`prokn` —" in md
    assert "`sawgraph` —" not in md


async def test_inline_queries_on_a_turn_still_render():
    md = await create_chat_transcript(
        model="m",
        exchanges=[
            {
                "prompt": "q",
                "queries": [
                    {
                        "sparql": "SELECT * {}",
                        "description": "inline",
                        "results": {"format": "csv", "text": "a,b\n1,2"},
                    }
                ],
            }
        ],
    )
    assert "#### Query 1 — inline" in md
    assert "```csv\na,b\n1,2\n```" in md


async def test_intermediate_query_rows_omitted_by_default():
    intermediate = {
        "vars": ["x"],
        "rows": [{"x": "step-1-value"}],
        "row_count": 1,
    }
    session.record(
        "SELECT ?x WHERE { GRAPH <https://purl.org/okn/frink/kg/prokn> { ?x ?p ?o } }",
        "json",
        result=intermediate,
    )
    session.record(
        "SELECT ?disease ?label WHERE { GRAPH <https://purl.org/okn/frink/kg/sawgraph> { ?x :linkedTo ?disease } }",
        "json",
        result=JSON_RESULT,
    )

    md = await create_chat_transcript(model="m")
    # Intermediate (first) query: text shown, rows omitted with a count note.
    assert "_1 row(s) — results omitted_" in md
    assert "step-1-value" not in md
    # Final query: full result table rendered.
    assert "| disease | label |" in md
    assert "| MONDO:0005240 | kidney cancer |" in md


async def test_include_intermediate_rows_true_renders_all():
    intermediate = {
        "vars": ["x"],
        "rows": [{"x": "step-1-value"}],
        "row_count": 1,
    }
    session.record(
        "SELECT ?x WHERE { GRAPH <https://purl.org/okn/frink/kg/prokn> { ?x ?p ?o } }",
        "json",
        result=intermediate,
    )
    session.record(
        "SELECT ?disease ?label WHERE { GRAPH <https://purl.org/okn/frink/kg/sawgraph> { ?x :linkedTo ?disease } }",
        "json",
        result=JSON_RESULT,
    )

    md = await create_chat_transcript(model="m", include_intermediate_rows=True)
    assert "results omitted" not in md
    assert "step-1-value" in md
    assert "| MONDO:0005240 | kidney cancer |" in md


async def test_date_defaults_to_today():
    from datetime import date

    md = await create_chat_transcript(model="m")
    assert f"**Date:** {date.today().isoformat()}" in md


async def test_no_queries_renders_placeholder():
    md = await create_chat_transcript(model="m")
    assert "_None queried._" in md
    assert "_No prompts recorded._" in md


async def test_json_format_includes_log():
    session.record(
        "SELECT ?s WHERE { GRAPH <https://purl.org/okn/frink/kg/prokn> { ?s ?p ?o } }",
        "json",
        result={"vars": ["s"], "rows": [{"s": "urn:x"}], "row_count": 1},
    )
    out = await create_chat_transcript(model="claude-opus-4-8", date="2026-05-31", format="json")
    assert out["model"] == "claude-opus-4-8"
    assert out["sparql_endpoint"] == FEDERATION_ENDPOINT
    assert len(out["query_log"]) == 1
    assert out["knowledge_graphs"] == [
        {"shortname": "prokn", "named_graph": "https://purl.org/okn/frink/kg/prokn"}
    ]


async def test_unsupported_format_returns_error():
    out = await create_chat_transcript(model="m", format="pdf")
    assert "error" in out


def test_rows_to_table_escapes_pipes():
    table = _rows_to_table(["c"], [{"c": "a|b"}])
    assert "| a\\|b |" in table


def test_session_records_and_dedupes_visualizations():
    session.record_visualization("dreamkg", "classDiagram\n  class A")
    session.record_visualization("spoke-genelab", "classDiagram\n  class B")
    # Re-visualizing the same KG replaces its diagram, keeping position + count.
    session.record_visualization("dreamkg", "classDiagram\n  class A2")
    viz = session.visualizations()
    assert [v["shortname"] for v in viz] == ["dreamkg", "spoke-genelab"]
    assert viz[0]["mermaid"].endswith("class A2")
    assert session.record_visualization("x", "") is None and len(session.visualizations()) == 2


async def test_transcript_renders_logged_visualization():
    session.record_visualization("spoke-genelab", "classDiagram\n  class Gene")
    md = await create_chat_transcript(model="m")
    assert "## Schema visualizations" in md
    assert "### `spoke-genelab` schema" in md
    assert "```mermaid" in md
    assert "classDiagram" in md
    # The diagram's KG is inferred into the knowledge-graphs section.
    assert "`spoke-genelab`" in md


async def test_visualization_kg_inference_and_json():
    session.record_visualization("dreamkg", "classDiagram\n  class Place")
    out = await create_chat_transcript(model="m", format="json")
    assert len(out["visualizations"]) == 1
    assert out["visualizations"][0]["shortname"] == "dreamkg"
    assert out["knowledge_graphs"] == [
        {"shortname": "dreamkg", "named_graph": "https://purl.org/okn/frink/kg/dreamkg"}
    ]


async def test_include_visualizations_false_omits_section():
    session.record_visualization("dreamkg", "classDiagram\n  class Place")
    md = await create_chat_transcript(model="m", include_visualizations=False)
    assert "## Schema visualizations" not in md


async def test_visualize_schema_returns_fenced_block_and_logs_raw(monkeypatch):
    import mcp_okn.server as srv

    async def fake_viz(shortname):
        return {"shortname": shortname, "mermaid": "classDiagram\n  class Gene"}

    monkeypatch.setattr(srv.schema, "visualize_schema", fake_viz)
    out = await srv.visualize_schema("demo")
    # Pre-fenced block for verbatim presentation; raw mermaid kept fence-free.
    assert out["mermaid_block"] == "```mermaid\nclassDiagram\n  class Gene\n```"
    assert not out["mermaid"].startswith("```")
    # The session logs the RAW diagram, so the transcript fences it exactly once.
    [viz] = session.visualizations()
    assert viz["mermaid"] == "classDiagram\n  class Gene"
    md = await create_chat_transcript(model="m")
    assert md.count("```mermaid") == 1


async def test_transcript_resource_publishes_last_markdown():
    # Before generating anything, the resource is a placeholder.
    assert "No transcript yet" in latest_transcript_resource()
    # After generating a markdown transcript, the resource serves it verbatim.
    md = await create_chat_transcript(
        model="m", exchanges=[{"prompt": "hi", "answer": "hello"}]
    )
    assert latest_transcript_resource() == md
    assert "👤 **User**" in latest_transcript_resource()
    # reset() clears it back to the placeholder.
    session.reset()
    assert "No transcript yet" in latest_transcript_resource()


async def test_json_transcript_does_not_publish_resource():
    # Only the markdown rendering is published to the resource.
    await create_chat_transcript(model="m", format="json")
    assert "No transcript yet" in latest_transcript_resource()


async def test_inline_mermaid_on_a_turn_renders():
    md = await create_chat_transcript(
        model="m",
        exchanges=[{"prompt": "show schema", "mermaid": "classDiagram\n  class Foo"}],
    )
    assert "```mermaid" in md
    assert "class Foo" in md
