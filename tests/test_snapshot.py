from mcp_okn.registry import load_snapshot


def test_snapshot_loads_and_is_clean():
    snap = load_snapshot()
    assert len(snap) > 30, "expected the bundled snapshot to contain all KGs"
    for kg in snap:
        # required, query-relevant fields present
        assert kg["shortname"]
        assert kg["named_graph"] == f"https://purl.org/okn/frink/kg/{kg['shortname']}"
        # per-KG Jena endpoints must never be bundled
        assert "sparql" not in kg
        assert "tpf" not in kg


def test_snapshot_has_known_kgs():
    names = {kg["shortname"] for kg in load_snapshot()}
    assert {"prokn", "ubergraph", "sawgraph"} <= names
