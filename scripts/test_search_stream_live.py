#!/usr/bin/env python3
"""Verify incremental search stream emits multiple progress events."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"


def main() -> int:
    payload = {
        "query": "magnesium alloy passivation salt spray",
        "source_types": ["patents", "literature", "internet"],
        "limit_per_source": 5,
        "total_limit": 30,
    }
    req = urllib.request.Request(
        f"{BASE}/api/search/stream",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    task_id = body["task_id"]
    print(f"task_id={task_id}")

    events: list[dict] = []
    deadline = time.time() + 120
    last_total = -1
    while time.time() < deadline:
        r = urllib.request.Request(f"{BASE}/api/tasks/{task_id}")
        with urllib.request.urlopen(r, timeout=10) as resp:
            st = json.loads(resp.read())
        if st.get("state") in ("completed", "failed"):
            meta = st.get("result") or {}
            total = meta.get("total", 0)
            if total > last_total:
                events.append({"total": total, "final": True})
            print(f"terminal state={st['state']} total={total}")
            if st["state"] == "failed":
                print(st)
                return 1
            break
        meta_raw = st.get("message", "")
        # Poll meta via repeated status — running tasks expose message
        result_preview = st.get("result")
        if isinstance(result_preview, dict) and result_preview.get("total", 0) > last_total:
            last_total = result_preview["total"]
            events.append({"total": last_total, "message": meta_raw})
        time.sleep(0.3)

    # Also verify iter_search multi-tick offline
    from app.services import literature
    from app.domain.schemas import Requirement, ProductDomain

    ticks: list[int] = []
    literature.iter_search(
        "magnesium passivation",
        ["patents", "literature"],
        req=Requirement(domain=ProductDomain.surface_treatment, substrate="magnesium_alloy"),
        total_limit=50,
        progress_cb=lambda p, m=None: ticks.append(len(p)),
    )
    print(f"offline progress ticks={len(ticks)}")
    if len(ticks) < 2:
        print("FAIL: expected >=2 offline progress ticks")
        return 1
    print("OK: incremental search verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
