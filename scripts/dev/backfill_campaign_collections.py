#!/usr/bin/env python3
"""P1 backfill: project-organise existing DOE campaigns into DataLab collections.

Usage: python scripts/dev/backfill_campaign_collections.py [campaign_id ...]
(no ids → campaigns 14..17, the datalab-era DOE campaigns)

Idempotent: sync_campaign_collection re-uses existing collections.
"""
from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
os.environ.setdefault(
    "FORMUMIND_ENV_FILE", os.path.join(_ROOT, "data", ".env.host")
)


async def main() -> None:
    from app.db.campaign_store import get_campaign_store

    ids = [int(a) for a in sys.argv[1:]] or list(range(14, 18))
    store = get_campaign_store()
    for cid in ids:
        result = await store.sync_campaign_collection(cid)
        print(f"campaign {cid} → {result}")
    if hasattr(store, "close"):
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
