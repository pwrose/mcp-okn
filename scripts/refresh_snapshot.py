"""Regenerate the bundled KG snapshot from the live okn-registry.

Run when the registry changes:

    uv run python scripts/refresh_snapshot.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp_okn import registry

OUT = Path(__file__).resolve().parent.parent / "src" / "mcp_okn" / "data" / "kgs.json"


async def main() -> None:
    kgs = await registry.list_kgs(refresh=True)
    kgs.sort(key=lambda k: k["shortname"])
    assert all("sparql" not in k and "tpf" not in k for k in kgs), "endpoint leaked!"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(kgs, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(kgs)} KGs to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
