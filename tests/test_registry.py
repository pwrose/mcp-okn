from mcp_okn import registry
from mcp_okn.registry import _split_frontmatter, _meta_from_front

SAMPLE = """\
---
template: overrides/kg.html
shortname: prokn
title: Protein Knowledge Network
description: ProKN integrates protein-centric data with CFDE datasets.
homepage: https://research.bioinformatics.udel.edu/ProKN/
sparql: https://apps.okn.us/prokn/sparql
tpf: https://apps.okn.us/ldf/prokn
---
The Protein Knowledge Network (ProKN), developed by the University of Delaware.
"""


def test_split_frontmatter_parses_yaml_and_body():
    front, body = _split_frontmatter(SAMPLE)
    assert front["shortname"] == "prokn"
    assert front["title"] == "Protein Knowledge Network"
    assert body.startswith("The Protein Knowledge Network")


def test_meta_drops_per_kg_endpoints_and_builds_named_graph():
    front, _ = _split_frontmatter(SAMPLE)
    meta = _meta_from_front("prokn", front)
    # The per-KG Jena endpoints must never be surfaced.
    assert "sparql" not in meta
    assert "tpf" not in meta
    assert meta["named_graph"] == "https://purl.org/okn/frink/kg/prokn"
    assert meta["description"].startswith("ProKN integrates")


def test_split_frontmatter_no_fence():
    front, body = _split_frontmatter("just text, no frontmatter")
    assert front == {}
    assert body == "just text, no frontmatter"


async def test_fetch_kg_long_description_returns_body(monkeypatch):
    async def fake_doc(shortname, client=None, refresh=False):
        assert shortname == "prokn"
        return SAMPLE

    monkeypatch.setattr(registry, "fetch_kg_doc", fake_doc)
    body = await registry.fetch_kg_long_description("prokn")
    # The free-text prose, not the YAML frontmatter.
    assert body.startswith("The Protein Knowledge Network")
    assert "shortname:" not in body


async def test_describe_kg_long_description_returns_only_prose(monkeypatch):
    from mcp_okn import server

    async def fake_doc(shortname, client=None, refresh=False):
        return SAMPLE

    monkeypatch.setattr(registry, "fetch_kg_doc", fake_doc)
    full = await server.describe_kg("prokn")
    prose = await server.describe_kg("prokn", long_description=True)
    # Full keeps the frontmatter; the long_description option strips it.
    assert "shortname: prokn" in full
    assert "shortname:" not in prose
    assert prose.startswith("The Protein Knowledge Network")
