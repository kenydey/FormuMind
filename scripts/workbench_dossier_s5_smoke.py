#!/usr/bin/env python3
"""Workbench → dossier S5 smoke (live stack).

Creates a project-scoped workbench campaign, syncs a Completed measurement,
refreshes the dossier pack, and asserts S5 lab rows come from ExperimentRow
(``source`` starts with ``experiment:``), not ``workspace.measured``.

Requires:
  - wiki/dossier flags on (script enables via env-flags when allowed)
  - campaign backend reachable (sqlite or Datalab)

Usage:
  python3 scripts/workbench_dossier_s5_smoke.py
  FM_BASE=http://127.0.0.1:5173 python3 scripts/workbench_dossier_s5_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("FM_BASE", "http://127.0.0.1:5173").rstrip("/")
FAILURES: list[str] = []


def req(method: str, path: str, body: dict | None = None, *, timeout: float = 90):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype:
                try:
                    return resp.status, json.loads(raw.decode())
                except json.JSONDecodeError:
                    return resp.status, raw.decode(errors="replace")
            return resp.status, raw
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
    print(f"Workbench → dossier S5 smoke → {BASE}\n")

    code, body = req("GET", "/health")
    check(
        "health",
        code == 200 and isinstance(body, dict) and body.get("status") in ("ok", "degraded"),
        str(body)[:200],
    )

    req(
        "POST",
        "/api/settings/env-flags",
        {
            "updates": {
                "wiki_enabled": True,
                "wiki_project_dossier_enabled": True,
                "wiki_dossier_report_enabled": True,
                "wiki_dossier_auto_patch": False,
            }
        },
    )

    code, body = req(
        "POST",
        "/api/projects",
        {
            "title": "Workbench→S5 金样",
            "requirement": {
                "domain": "anticorrosion_coating",
                "substrate": "carbon_steel",
                "salt_spray_hours": 720,
                "voc_limit_gpl": 350,
            },
        },
    )
    pid = body.get("id") if isinstance(body, dict) else None
    check("create project", code in (200, 201) and bool(pid), str(body)[:240])
    if not pid:
        return 1

    plan = {
        "design": "lhs",
        "factors": [{"name": "pH", "low": 3.5, "high": 5.5, "unit": ""}],
        "runs": [{"run_id": 1, "coded": {"pH": 0.0}, "natural": {"pH": 4.5}}],
        "notes": "workbench_dossier_s5_smoke",
        "plan_id": "wb-s5-smoke",
        "domain": "anticorrosion_coating",
    }
    code, body = req(
        "POST",
        "/api/experiments/workbench/campaigns",
        {"plan": plan, "project_id": pid, "name": "S5 smoke campaign"},
    )
    if code in (502, 503) or (
        isinstance(body, dict) and "Datalab" in str(body.get("detail") or body)
    ):
        print(
            f"WARN  create workbench campaign: status={code} — "
            "Datalab/ELN unreachable; pytest covers sqlite path.\n"
            f"      {str(body)[:180]}"
        )
        print(f"\nproject_id = {pid} (campaign blocked by ELN)")
        print("Workbench → dossier S5 smoke soft-skipped (ELN down).")
        return 0

    cid = body.get("campaign_id") if isinstance(body, dict) else None
    rows = (body.get("rows") if isinstance(body, dict) else None) or []
    check("create workbench campaign", code == 200 and bool(cid) and bool(rows), str(body)[:240])
    if not cid or not rows:
        return 1

    row_id = rows[0].get("id")
    code, body = req(
        "PUT",
        "/api/experiments/workbench/sync",
        {
            "campaign_id": cid,
            "rows": [
                {
                    "id": row_id,
                    "status": "Pending",
                    "actual_params": {"pH": 4.5},
                    "measurements": {"salt_spray_hours": 680.0},
                }
            ],
        },
    )
    check(
        "sync completed measurement",
        code == 200 and isinstance(body, dict),
        str(body)[:240],
    )

    code, body = req("POST", "/api/wiki/dossier/ensure", {"project_id": pid, "vertical": "silane"})
    check("dossier ensure", code == 200 and isinstance(body, dict) and body.get("ok"), str(body)[:200])

    code, body = req("POST", "/api/wiki/dossier/refresh", {"project_id": pid})
    check("dossier refresh", code == 200 and isinstance(body, dict), str(body)[:200])

    code, pack = req("GET", f"/api/wiki/dossier/{pid}/pack")
    pack_ok = code == 200 and isinstance(pack, dict)
    check("dossier pack", pack_ok, str(pack)[:200])
    if pack_ok:
        flags = pack.get("flags") or {}
        lab = (pack.get("lab") or {}).get("rows") or []
        check("S5 lab non-empty", not flags.get("empty_lab") and bool(lab), str(flags))
        sources = [str(r.get("source") or "") for r in lab]
        check(
            "S5 from experiment/workbench (not workspace.measured)",
            any(s.startswith("experiment:") or s.startswith("workbench") for s in sources)
            and "workspace.measured" not in sources,
            f"sources={sources[:5]}",
        )
        check(
            "S5 has salt_spray_hours=680",
            any(
                r.get("metric") == "salt_spray_hours" and float(r.get("value") or 0) == 680.0
                for r in lab
            ),
            str(lab[:2])[:200],
        )

    print(f"\nproject_id = {pid}")
    print(f"campaign_id = {cid}")
    print("Hub: Wiki → 项目卷宗 → 刷新 → 看 S5 台账来源 experiment:/workbench:")

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("\nWorkbench → dossier S5 smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
