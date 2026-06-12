"""Regenerate the bundled KG snapshot from the live okn-registry, and sync the
precomputed crosswalk table from its editable source.

Run when the registry changes or `metadata/crosswalks.json` is edited:

    uv run python scripts/refresh_snapshot.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp_okn import registry

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "mcp_okn" / "data"
OUT = DATA / "kgs.json"
CROSSWALK_SRC = ROOT / "metadata" / "crosswalks.json"
CROSSWALK_OUT = DATA / "crosswalks.json"


def sync_crosswalks(known: set[str]) -> None:
    """Copy metadata/crosswalks.json into the package and validate it.

    The editable source of record lives in metadata/; the bundled copy is what
    ships in the wheel. Every KG the table names (bridges included) must be a
    real, servable shortname, or a join recipe would point at nothing.
    """
    if not CROSSWALK_SRC.exists():
        print(f"no crosswalk source at {CROSSWALK_SRC}; skipping")
        return
    data = json.loads(CROSSWALK_SRC.read_text(encoding="utf-8"))
    referenced: set[str] = set()
    for e in data.get("verified_crosswalks", []):
        for key in ("left_kg", "right_kg", "bridge_kg"):
            if e.get(key):
                referenced.add(e[key])
        referenced.update(e.get("members", []))
    unknown = referenced - known
    assert not unknown, f"crosswalk table names unknown KGs: {sorted(unknown)}"
    with CROSSWALK_OUT.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(
        f"synced {len(data.get('verified_crosswalks', []))} crosswalks to "
        f"{CROSSWALK_OUT}"
    )


async def main() -> None:
    kgs = await registry.list_kgs(refresh=True)
    kgs.sort(key=lambda k: k["shortname"])
    assert all("sparql" not in k and "tpf" not in k for k in kgs), "endpoint leaked!"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(kgs, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(kgs)} KGs to {OUT}")
    sync_crosswalks({k["shortname"] for k in kgs})


if __name__ == "__main__":
    asyncio.run(main())
