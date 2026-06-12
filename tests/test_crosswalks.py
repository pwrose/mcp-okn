import json

import pytest

import mcp_okn.crosswalks as cw
from mcp_okn.registry import load_snapshot
from mcp_okn.server import get_join_strategy, list_crosswalks


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


@pytest.mark.asyncio
async def test_list_crosswalks_returns_every_verified_entry():
    out = await list_crosswalks()
    expected = len(cw.load_crosswalks()["verified_crosswalks"])
    assert out["count"] == expected
    assert len(out["crosswalks"]) == expected
    assert all(row["kgs"] for row in out["crosswalks"])  # every join names KGs


@pytest.mark.asyncio
async def test_list_crosswalks_examples_toggle():
    with_ex = await list_crosswalks()  # default include_examples=True
    assert all("example_question" in row for row in with_ex["crosswalks"])
    without_ex = await list_crosswalks(include_examples=False)
    assert all("example_question" not in row for row in without_ex["crosswalks"])


@pytest.mark.asyncio
async def test_list_crosswalks_uses_official_kg_shortnames():
    """Every KG named in the listing must be an official registry shortname (the
    same id `list_kgs`/`describe_kg`/`query` accept), never a table-local alias."""
    official = {k["shortname"] for k in load_snapshot()}
    out = await list_crosswalks()
    used = {kg for row in out["crosswalks"] for kg in row["kgs"]}
    assert used, "no KGs surfaced"
    assert used <= official, f"non-official shortnames: {sorted(used - official)}"
    # The table `id` (e.g. "M2-mesh-spokeokn") embeds non-official KG
    # abbreviations, so it must not appear in the listing.
    assert all("id" not in row for row in out["crosswalks"])


@pytest.mark.asyncio
async def test_list_crosswalks_carries_verified_date():
    out = await list_crosswalks()
    assert out["verified_on"] == cw.verified_on()
    assert out["verified_on"] is not None


@pytest.mark.asyncio
async def test_get_join_strategy_returns_skeleton_not_recipe():
    """The retrieval tool guides queries with the runnable skeleton_query and
    omits the prose iri_normalization recipe (the skeleton encodes it)."""
    out = await get_join_strategy("biobricks-aopwiki", "biobricks-toxcast")
    assert out["status"] == "verified"
    j = out["joins"][0]
    assert "skeleton_query" in j and "COUNT(" in j["skeleton_query"]
    assert "iri_normalization" not in j
    # The single-KG listing form drops the recipe too.
    listing = await get_join_strategy("biobricks-aopwiki")
    assert all("iri_normalization" not in e for e in listing["joins"])
    assert any("skeleton_query" in e for e in listing["joins"])


def test_island_status_for_island_kg():
    assert cw.island_status("maudekg") is not None
    assert cw.island_status("maudekg")["island"] is True
    assert cw.island_status("prokn") is None  # not an island


def test_thin_thread_kg_surfaces_threads_without_being_an_island():
    status = cw.island_status("ruralkg")
    assert status is not None
    assert status["island"] is False
    assert status["thin_threads"]


def test_skeleton_queries_are_well_formed():
    """Every bundled skeleton_query must be a runnable COUNT join that scopes the
    entry's KGs with named GRAPH blocks, and carry honest verification metadata."""
    data = cw.load_crosswalks()
    skeletons = [e for e in data["verified_crosswalks"] if e.get("skeleton_query")]
    assert len(skeletons) >= 50, f"only {len(skeletons)} skeletons; expected most of 61"
    for e in skeletons:
        q = e["skeleton_query"]
        assert "SELECT" in q and "COUNT(" in q, e["id"]
        # The endpoints it joins must each appear as a scoped named graph.
        for kg in cw._entry_kgs(e):
            if kg in ("ubergraph", "wikidata"):  # bridges aren't always GRAPH-scoped by id
                continue
            assert f"/kg/{kg}>" in q, f"{e['id']} skeleton omits graph {kg}"
        assert e.get("skeleton_verified") in (True, False), e["id"]
        # Near-misses must disclose what they actually returned.
        if e["skeleton_verified"] is False:
            assert "skeleton_returns" in e, e["id"]


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
