#!/usr/bin/env python3
"""E2E: magnesium alloy passivation — search → requirements → recommend → DOE → workbench."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
FAILURES: list[str] = []
LOG: list[str] = []


def log(msg: str) -> None:
    print(msg)
    LOG.append(msg)


def req(method: str, path: str, body: dict | None = None, timeout: float = 180) -> tuple[int, dict | list | str]:
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


def check(step: str, ok: bool, detail: str = "") -> None:
    if ok:
        log(f"  ✓ {step}")
    else:
        log(f"  ✗ {step}: {detail[:500]}")
        FAILURES.append(f"{step}: {detail}")


def poll_task(task_id: str, timeout_s: float = 120) -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        code, body = req("GET", f"/api/tasks/{task_id}")
        if code != 200 or not isinstance(body, dict):
            time.sleep(0.5)
            continue
        state = body.get("state")
        if state in ("completed", "failed"):
            return body
        time.sleep(0.5)
    return None


def main() -> int:
    log("=== 镁合金表面钝化剂 E2E ===\n")

    # 1. Health
    code, health = req("GET", "/health")
    check("health", code == 200 and isinstance(health, dict), str(health))

    # 2. Literature search
    search_query = (
        "magnesium alloy passivation conversion coating neutral salt spray corrosion inhibitor "
        "chromate-free pretreatment powder coating paint adhesion"
    )
    code, search = req(
        "POST",
        "/api/search",
        {
            "query": search_query,
            "source_types": ["literature", "internet", "patents"],
            "limit_per_source": 8,
            "total_limit": 20,
        },
        timeout=180,
    )
    evidence = search.get("evidence", []) if isinstance(search, dict) else []
    check("literature search", code == 200 and len(evidence) > 0, f"code={code} total={search.get('total') if isinstance(search,dict) else search}")

    # 3. Technical requirement + project
    requirement = {
        "project_id": "mg_passivation_128h",
        "product_type": "镁合金表面钝化剂",
        "application": "镁合金裸膜防护或后接喷粉/喷漆前处理",
        "domain": "surface_treatment",
        "substrate": "magnesium_alloy",
        "salt_spray_hours": 128,
        "film_weight_gsm": 5.0,
        "cure_temperature_c": 50,
        "notes": "使用温度20-50°C；裸膜中性盐雾≥128h；可裸膜防护也可后接喷粉或喷漆",
        "objectives": [
            {"metric": "salt_spray_hours", "weight": 0.5, "direction": "maximize", "unit": "h"},
            {"metric": "coating_weight_gsm", "weight": 0.2, "direction": "maximize", "unit": "g/m²"},
            {"metric": "cost_cny_per_kg", "weight": 0.3, "direction": "minimize", "unit": "CNY/kg"},
        ],
        "constraints": {"use_temp_min_c": 20, "use_temp_max_c": 50},
    }

    code, project = req(
        "POST",
        "/api/projects",
        {"title": "镁合金表面钝化剂开发 (NSS 128h)", "requirement": requirement},
    )
    project_id = project.get("id") if isinstance(project, dict) else None
    check("create project", code == 200 and project_id, str(project)[:300])

    # 4. Sync research (CRAG + recommend)
    code, research = req(
        "POST",
        "/api/research",
        {**requirement, "query": search_query, "sources": evidence[:12]},
        timeout=120,
    )
    recs = research.get("recommended", []) if isinstance(research, dict) else []
    check("research recommend", code == 200 and len(recs) > 0, str(research)[:400])

    # 5. LLM formulation recommend
    code, form_rec = req(
        "POST",
        "/api/formulations/recommend",
        {"requirement": requirement, "sources": evidence[:10], "n": 3},
        timeout=180,
    )
    formulas = form_rec.get("formulas", []) if isinstance(form_rec, dict) else []
    check("formulation recommend", code == 200 and len(formulas) > 0, str(form_rec)[:400])

    # 6. DOE design
    code, doe = req(
        "POST",
        "/api/doe?design=lhs&engine=auto&n=6",
        requirement,
        timeout=60,
    )
    runs = doe.get("runs", []) if isinstance(doe, dict) else []
    plan_id = doe.get("plan_id") if isinstance(doe, dict) else None
    check("DOE lhs design", code == 200 and len(runs) >= 4, f"runs={len(runs)} plan_id={plan_id}")

    # 7. Workbench campaign (experiment ledger)
    if isinstance(doe, dict):
        doe["domain"] = requirement["domain"]
        if not doe.get("plan_id"):
            doe["plan_id"] = "mgpass01"
    code, campaign = req(
        "POST",
        "/api/experiments/workbench/campaigns",
        {
            "plan": doe,
            "strategy": "DOE-lhs",
            "requirement": requirement,
            "project_id": project_id,
            "name": "镁合金钝化剂 DOE — NSS 128h",
        },
        timeout=60,
    )
    campaign_id = campaign.get("campaign_id") if isinstance(campaign, dict) else None
    rows = campaign.get("rows", []) if isinstance(campaign, dict) else []
    check(
        "workbench campaign",
        code == 200 and campaign_id and len(rows) == len(runs),
        str(campaign)[:400],
    )

    # 8. Sync one row measurement + verify ledger
    if campaign_id and rows:
        row = rows[0]
        code, sync = req(
            "PUT",
            "/api/experiments/workbench/sync",
            {
                "campaign_id": campaign_id,
                "rows": [
                    {
                        "id": row["id"],
                        "status": "Completed",
                        "actual_params": row.get("planned_params") or row.get("actual_params"),
                        "measurements": {
                            "salt_spray_hours": 130.0,
                            "coating_weight_gsm": 6.2,
                            "cost_cny_per_kg": 18.5,
                        },
                    }
                ],
            },
        )
        check("workbench sync measurement", code == 200, str(sync)[:300])

        code, ledger = req("GET", f"/api/experiments/workbench/{campaign_id}")
        ledger_rows = ledger.get("rows", []) if isinstance(ledger, dict) else []
        completed = [r for r in ledger_rows if r.get("status") == "Completed"]
        check("ledger read-back", code == 200 and len(completed) >= 1, str(ledger)[:300])

    # 9. Async search stream (optional path)
    code, task = req(
        "POST",
        "/api/search/stream",
        {"query": "magnesium passivation chromate free", "source_types": ["literature"], "limit_per_source": 3},
        timeout=30,
    )
    task_id = task.get("task_id") if isinstance(task, dict) else None
    if code == 202 and task_id:
        result = poll_task(task_id, 90)
        check("async search stream", result and result.get("state") == "completed", str(result)[:200])
    else:
        check("async search stream", code == 202, str(task)[:200])

    log("")
    if FAILURES:
        log(f"FAILED {len(FAILURES)} step(s):")
        for f in FAILURES:
            log(f"  - {f}")
        return 1
    log("ALL E2E STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
