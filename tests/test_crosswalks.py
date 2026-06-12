import json

import pytest

import mcp_okn.crosswalks as cw
from mcp_okn.registry import load_snapshot
from mcp_okn.server import get_join_strategy


def test_table_loads_and_is_dated():
    data = cw.load_crosswalks()
    assert isinstance(data, dict)
    assert data.get("verified_crosswalks")
    assert cw.verified_on()  # a YYYY-MM-DD stamp for staleness visibility


def test_join_between_is_order_insensitive():
    a = cw.join_between("biobricks-aopwiki", "rdkg")
    b = cw.join_between("rdkg", "biobricks-aopwiki")
    assert a and a == b


def test_join_recipe_carries_the_fields_needed_to_build_sparql():
    (recipe,) = cw.join_between("biobricks-aopwiki", "rdkg")
    for field in (
        "left_predicate",
        "right_predicate",
        "left_role",
        "right_role",
        "shared_key",
        "key_namespace",
        "verified_count",
    ):
        assert field in recipe


def test_bridged_pair_surfaces_via_bridge_kg():
    # spoke-okn reaches rdkg only through ubergraph (DOID<->MONDO, entry A10).
    joins = cw.join_between("spoke-okn", "rdkg")
    assert any(j.get("bridge_kg") == "ubergraph" for j in joins)


@pytest.mark.asyncio
async def test_verified_pair_returns_recipe():
    out = await get_join_strategy("biobricks-aopwiki", "rdkg")
    assert out["status"] == "verified"
    assert out["joins"]
    assert out["verified_on"]


@pytest.mark.asyncio
async def test_known_non_join_pair_is_flagged_not_verified():
    # SAWGraph owl:sameAs -> geoconnex reference IRIs are not materialized (0 rows).
    out = await get_join_strategy("sawgraph", "geoconnex")
    assert out["status"] == "known_non_join"
    assert out["non_joins"]
    assert "diagnosis" in out["non_joins"][0]


@pytest.mark.asyncio
async def test_single_kg_non_join_blocks_any_pairing():
    # digcfdekg is unmaterialized at the endpoint: nothing to join, with anything.
    out = await get_join_strategy("digcfdekg", "prokn")
    assert out["status"] == "known_non_join"


@pytest.mark.asyncio
async def test_unknown_pair_routes_to_find_crosswalks():
    out = await get_join_strategy("prokn", "securechainkg")
    assert out["status"] == "unknown"
    assert "find_crosswalks" in out["note"]


@pytest.mark.asyncio
async def test_single_kg_listing_returns_all_its_joins():
    out = await get_join_strategy("spoke-okn")
    assert "status" not in out  # listing form, not a pair verdict
    assert out["joins"]
    assert all("spoke-okn" in cw._entry_kgs(j) for j in out["joins"])


def test_island_status_for_island_kg():
    assert cw.island_status("maudekg") is not None
    assert cw.island_status("maudekg")["island"] is True
    assert cw.island_status("prokn") is None  # not an island


def test_thin_thread_kg_surfaces_threads_without_being_an_island():
    status = cw.island_status("ruralkg")
    assert status is not None
    assert status["island"] is False
    assert status["thin_threads"]


def test_every_referenced_kg_exists_in_the_registry_snapshot():
    """Guard future edits to the table: no recipe may name a KG the server can't
    serve (bridges ubergraph/wikidata included)."""
    known = {k["shortname"] for k in load_snapshot()}
    data = cw.load_crosswalks()
    referenced: set[str] = set()
    for e in data.get("verified_crosswalks", []):
        referenced |= cw._entry_kgs(e)
    assert referenced <= known, f"unknown KGs in table: {sorted(referenced - known)}"


def test_bundled_table_matches_metadata_source(tmp_path):
    """The packaged copy must stay in sync with the editable source of record."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    source = repo / "metadata" / "crosswalks.json"
    if not source.exists():
        pytest.skip("metadata source not present in this checkout")
    assert json.loads(source.read_text()) == cw.load_crosswalks()
