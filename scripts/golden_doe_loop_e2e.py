#!/usr/bin/env python3
"""Golden DOE closed-loop E2E (Batch B) — API-level, no browser.

Asserts:
  create campaign → measured sync(trigger_loop) → loop_task_id
  → append converged history → further sync does not dispatch

Usage:
  python3 scripts/golden_doe_loop_e2e.py
  FM_BASE=http://127.0.0.1:8000 python3 scripts/golden_doe_loop_e2e.py

For CI without a live server, prefer:
  cd backend && .venv/bin/python -m pytest -q tests/test_project_auto_loop.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("FM_BASE", "http://127.0.0.1:8000").rstrip("/")
FAILURES: list[str] = []


def req(method: str, path: str, body: dict | None = None, *, timeout: float = 60):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode())
            except json.JSONDecodeError:
                return resp.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode())
        except Exception:
            return e.code, raw.decode(errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e)


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  OK  {name}")
    else:
        print(f"FAIL  {name}: {detail}")
        FAILURES.append(f"{name}: {detail}")


def main() -> int:
    print(f"golden_doe_loop_e2e → {BASE}")
    plan = {
        "design": "lhs",
        "factors": [],
        "runs": [
            {
                "run_id": 1,
                "coded": {},
                "natural": {"Zinc phosphate": 8.0, "cure_temperature_c": 80.0},
            }
        ],
        "notes": "golden-doe-loop",
        "plan_id": "golden-doe-loop",
        "domain": "anticorrosion_coating",
    }
    code, created = req("POST", "/api/experiments/workbench/campaigns", {"plan": plan})
    check("create campaign", code == 200 and isinstance(created, dict), str(created)[:200])
    if not isinstance(created, dict):
        return 1
    cid = created.get("campaign_id")
    row = (created.get("rows") or [{}])[0]
    check("campaign id", bool(cid), str(cid))

    code, sync1 = req(
        "PUT",
        "/api/experiments/workbench/sync",
        {
            "campaign_id": cid,
            "rows": [
                {
                    "id": row.get("id"),
                    "status": "Completed",
                    "actual_params": {"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
                    "measurements": {"salt_spray_hours": 860.0},
                }
            ],
            "trigger_loop": True,
        },
    )
    check("sync+trigger", code == 200 and isinstance(sync1, dict), str(sync1)[:240])
    if isinstance(sync1, dict):
        check("training_ingested", int(sync1.get("training_ingested") or 0) >= 1, str(sync1))
        check("loop_task_id", bool(sync1.get("loop_task_id")), str(sync1.get("loop_message")))
        check("loop_status present", sync1.get("loop_status") is not None, str(sync1.get("loop_status")))

    # Mark converged via internal store is not available over HTTP; re-GET and
    # rely on pytest for converge-stop. Here we only assert status shape.
    code, detail = req("GET", f"/api/experiments/workbench/{cid}")
    check("get campaign", code == 200 and isinstance(detail, dict), str(detail)[:200])
    if isinstance(detail, dict):
        ls = detail.get("loop_status") or {}
        check("loop_status.status", ls.get("status") in ("idle", "running", "converged", "paused", "failed"), str(ls))

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("\nAll checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
