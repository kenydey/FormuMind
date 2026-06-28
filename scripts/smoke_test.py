#!/usr/bin/env python3
"""Production smoke tests against running FormuMind stack."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:5173"
FAILURES: list[str] = []


def req(method: str, path: str, body: dict | None = None, timeout: float = 120) -> tuple[int, dict | str]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  OK  {name}")
    else:
        print(f"FAIL  {name}: {detail}")
        FAILURES.append(f"{name}: {detail}")


def main() -> int:
    print(f"Smoke testing {BASE}\n")

    code, body = req("GET", "/health")
    check("health", code == 200 and isinstance(body, dict) and body.get("status") in ("ok", "degraded"),
          str(body)[:200])

    code, body = req("GET", "/api/meta")
    check("meta", code == 200 and "domains" in body, str(body)[:120])

    code, body = req("GET", "/api/ingredients")
    check("ingredients", code == 200 and "Xylene" in body, str(body)[:120])

    code, body = req("POST", "/api/research", {
        "domain": "degreaser", "substrate": "carbon_steel",
        "cleaning_efficiency": 95, "ph_target": 12.5,
    })
    check("research", code == 200 and body.get("recommended"), str(body)[:120])

    code, body = req("POST", "/api/doe?design=plackett_burman", {
        "domain": "anticorrosion_coating", "cure_temperature_c": 90,
    })
    check("doe", code == 200 and body.get("runs"), str(body)[:120])

    code, body = req("POST", "/api/optimize", {
        "requirement": {"domain": "anticorrosion_coating", "salt_spray_hours": 500},
        "iterations": 6,
    })
    check("optimize submit", code == 202 and body.get("task_id"), str(body)[:120])
    task_id = body.get("task_id") if isinstance(body, dict) else None
    if task_id:
        state = "pending"
        for _ in range(60):
            _, tb = req("GET", f"/api/tasks/{task_id}")
            state = tb.get("state") if isinstance(tb, dict) else state
            if state in ("completed", "failed"):
                break
            time.sleep(0.5)
        check("optimize poll", state == "completed", f"state={state}")

    code, body = req("POST", "/api/search", {
        "query": "zinc phosphate corrosion coating",
        "source_types": ["internet"],
        "limit_per_source": 3,
        "total_limit": 5,
    }, timeout=120)
    evidence = body.get("evidence") if isinstance(body, dict) else None
    check("search internet", code == 200 and isinstance(evidence, list), str(body)[:200])

    code, body = req("GET", "/api/settings/secrets")
    check("settings secrets", code == 200, str(body)[:120])

    plan = {
        "design": "lhs", "factors": [],
        "runs": [{"run_id": 1, "coded": {}, "natural": {"Zinc phosphate": 8.0}}],
        "notes": "smoke", "plan_id": "smoke01", "domain": "anticorrosion_coating",
    }
    code, body = req("POST", "/api/experiments/workbench/campaigns", {
        "plan": plan, "strategy": "DOE-lhs",
    })
    check("workbench campaign", code == 200 and body.get("campaign_id"), str(body)[:200])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
