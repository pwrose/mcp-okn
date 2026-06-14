import json

import pytest

import mcp_okn.crosswalks as cw
from mcp_okn.registry import load_snapshot
from mcp_okn.server import _complementary_note, get_join_strategy, list_crosswalks


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
async def test_list_crosswalks_collapses_taxon_hub_into_one_row():
    entries = cw.load_crosswalks()["verified_crosswalks"]
    spokes = [e for e in entries if cw._is_taxon_hub_spoke(e)]
    assert len(spokes) >= 2, "expected several KG<->ubergraph taxon spokes to collapse"

    out = await list_crosswalks()
    rows = out["crosswalks"]
    assert out["count"] == len(rows)
    assert all(row["kgs"] for row in rows)  # every row names KGs

    # the spokes collapse into exactly one hub row; all other entries stay as rows
    hub_rows = [r for r in rows if r.get("hub")]
    assert len(hub_rows) == 1
    assert len(rows) == (len(entries) - len(spokes)) + 1

    hub = hub_rows[0]
    assert hub["hub"] == "ubergraph"
    assert hub["shared_key"] == "NCBITaxon" and hub["domain"] == "Taxonomy"
    assert hub["bridge_kg"] is None and hub["verified_count"] is None
    # names every spoke's non-ubergraph member, and never the hub plumbing itself
    members = {kg for e in spokes for kg in (e["left_kg"], e["right_kg"]) if kg != "ubergraph"}
    assert set(hub["kgs"]) == members and "ubergraph" not in hub["kgs"]
    assert "taxon_overlap" in hub["note"]

    # a pairwise taxon crosswalk that bridges through ubergraph (D9) is NOT collapsed
    d9 = [r for r in rows if r["shared_key"] == "NCBITaxon" and r["bridge_kg"] == "ubergraph"]
    assert d9 and all(set(r["kgs"]) >= {"spoke-genelab", "spoke-okn"} for r in d9)


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
async def test_list_crosswalks_grouped_by_domain_and_sorted():
    """Rows carry a domain and are sorted by (domain, shared_key) so the listing
    renders as a table grouped by domain. Every shared_key must map to a real
    domain (not the "Other" fallback), so new keys force a mapping update."""
    out = await list_crosswalks()
    rows = out["crosswalks"]
    assert all(r.get("domain") for r in rows)
    assert all(r["domain"] != "Other" for r in rows), "unmapped shared_key domain"
    keys = [(r["domain"], r["shared_key"] or "", r["kgs"]) for r in rows]
    assert keys == sorted(keys), "rows not sorted by (domain, shared_key, kgs)"


@pytest.mark.asyncio
async def test_list_crosswalks_orders_bridge_in_the_middle():
    """For a bridged join the bridge KG sits between the two endpoints, not at an
    alphabetical end (e.g. oard-kg → ubergraph → prokn, not → prokn → ubergraph)."""
    out = await list_crosswalks()
    bridged = [r for r in out["crosswalks"] if r["bridge_kg"]]
    assert bridged, "expected at least one bridged crosswalk"
    for r in bridged:
        kgs = r["kgs"]
        assert kgs[1] == r["bridge_kg"], (r["bridge_kg"], kgs)
        assert len(kgs) == 3


@pytest.mark.asyncio
async def test_list_crosswalks_carries_verified_date():
    out = await list_crosswalks()
    assert out["verified_on"] == cw.verified_on()
    assert out["verified_on"] is not None


@pytest.mark.asyncio
async def test_get_join_strategy_joins_carry_domain_and_group():
    """Joins carry a domain and a multi-join listing is grouped by domain (sorted
    by (domain, shared_key)), consistent with list_crosswalks."""
    listing = await get_join_strategy("spoke-okn")  # touches many domains
    joins = listing["joins"]
    assert len(joins) > 1
    assert all(j.get("domain") and j["domain"] != "Other" for j in joins)
    keys = [(j["domain"], j["shared_key"] or "", j.get("id") or "") for j in joins]
    assert keys == sorted(keys), "single-KG joins not grouped by domain"
    pair = await get_join_strategy("oard-kg", "prokn")
    assert all("domain" in j for j in pair["joins"])


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


def test_complementary_note_fires_only_for_two_tagged_linkages():
    assert _complementary_note([]) is None
    assert _complementary_note([{"shared_key": "MONDO", "complementary_note": "x"}]) is None
    # An untagged second linkage (e.g. HP phenotypes) must not trigger it.
    assert _complementary_note(
        [{"shared_key": "MONDO", "complementary_note": "x"}, {"shared_key": "HP"}]
    ) is None
    note = _complementary_note(
        [
            {"shared_key": "MONDO", "complementary_note": "direct"},
            {"shared_key": "MONDO<->OMIM (bridged)", "complementary_note": "bridge"},
        ]
    )
    assert note is not None
    assert "COMPLEMENTARY" in note and "UNION" in note
    assert "MONDO" in note and "OMIM" in note


@pytest.mark.asyncio
async def test_oardkg_prokn_disease_linkages_flagged_complementary():
    """oard-kg↔prokn has a direct MONDO join AND an OMIM-via-ubergraph bridge that
    reach distinct disease sets; the pair result must flag them as complementary
    and each carry its own complementary_note (the cross-link)."""
    out = await get_join_strategy("oard-kg", "prokn")
    assert out["status"] == "verified"
    assert "COMPLEMENTARY" in out["note"] and "UNION" in out["note"]
    tagged = {
        j["shared_key"]: j["complementary_note"]
        for j in out["joins"]
        if j.get("complementary_note")
    }
    assert "MONDO" in tagged
    assert any("OMIM" in k for k in tagged)
    # Each tagged recipe names the other path, so the link is navigable.
    assert "OMIM" in tagged["MONDO"]


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
