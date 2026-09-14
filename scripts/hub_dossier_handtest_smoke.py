#!/usr/bin/env python3
"""Hub / API smoke for Project Dossier + Report trust boundary.

Run against a live FormuMind stack (Vite proxy → API), with flags enabled:

  FORMUMIND_WIKI_ENABLED=true
  FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED=true
  FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED=true
  FORMUMIND_WIKI_DOE_CONSTRAINTS=true   # default true; keep on for soft-hint check

Usage:
  python scripts/hub_dossier_handtest_smoke.py
  FM_BASE=http://127.0.0.1:5173 python scripts/hub_dossier_handtest_smoke.py

Exits 0 on success. Prints a Hub UI checklist reminder at the end.
Companion checklist: docs/plans/2026-09-14-wiki-hub-dossier-handtest.md
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("FM_BASE", "http://127.0.0.1:5173").rstrip("/")
FAILURES: list[str] = []


def req(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    timeout: float = 60,
) -> tuple[int, dict | str | bytes]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype or path.endswith("/pack"):
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
    print(f"Hub dossier/report smoke → {BASE}\n")

    code, body = req("GET", "/health")
    check(
        "health",
        code == 200 and isinstance(body, dict) and body.get("status") in ("ok", "degraded"),
        str(body)[:200],
    )

    code, body = req(
        "POST",
        "/api/projects",
        {
            "title": "Hub手测卷宗项目",
            "requirement": {
                "domain": "anticorrosion_coating",
                "substrate": "carbon_steel",
                "salt_spray_hours": 500,
                "voc_limit_gpl": 420,
            },
        },
    )
    pid = body.get("id") if isinstance(body, dict) else None
    if pid is None and isinstance(body, dict):
        pid = (body.get("project") or {}).get("id") or body.get("project_id")
    check("create project", code in (200, 201) and bool(pid), str(body)[:240])
    if not pid:
        print("\nCannot continue without project_id. Enable API and retry.")
        return 1

    code, body = req(
        "POST",
        "/api/wiki/dossier/ensure",
        {"project_id": pid, "vertical": "silane"},
    )
    if code in (403, 404, 409) or (
        isinstance(body, dict) and body.get("detail") and code != 200
    ):
        detail = body if not isinstance(body, dict) else body.get("detail", body)
        check(
            "dossier ensure (flags)",
            False,
            f"status={code} detail={str(detail)[:200]} — "
            "set FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED=true",
        )
    else:
        check(
            "dossier ensure",
            code == 200
            and isinstance(body, dict)
            and body.get("ok") is True
            and str(body.get("path") or "").startswith("themes/project-"),
            str(body)[:240],
        )

    code, body = req("GET", f"/api/wiki/dossier/{pid}")
    check(
        "dossier get",
        code == 200 and isinstance(body, dict) and (body.get("path") or body.get("ok")),
        str(body)[:200],
    )

    code, body = req("POST", "/api/wiki/dossier/refresh", {"project_id": pid})
    check(
        "dossier refresh",
        code == 200 and isinstance(body, dict) and body.get("ok") is not False,
        str(body)[:200],
    )

    code, body = req("GET", f"/api/wiki/dossier/{pid}/pack")
    pack_ok = code == 200 and isinstance(body, dict)
    check("dossier pack", pack_ok, str(body)[:200])
    if pack_ok:
        has_req = bool(body.get("requirements") or body.get("S1_requirements"))
        check("pack has requirements slice", has_req, "missing requirements in pack")

    code, body = req("GET", "/api/wiki/reports/templates")
    tpls = (body.get("templates") if isinstance(body, dict) else None) or []
    ids = {t.get("id") for t in tpls if isinstance(t, dict)}
    check(
        "report templates",
        code == 200 and {"briefing", "feasibility"} <= ids,
        str(body)[:200],
    )

    code, body = req(
        "POST",
        "/api/wiki/dossier/report",
        {
            "project_id": pid,
            "template": "briefing",
            "ensure_dossier": True,
            "persist": True,
        },
    )
    if code in (403, 404, 409):
        check(
            "report generate (flags)",
            False,
            f"status={code} — set FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED=true",
        )
        disclaimer = None
        rpath = None
    else:
        disclaimer = body.get("disclaimer") if isinstance(body, dict) else None
        rpath = body.get("path") if isinstance(body, dict) else None
        check(
            "report generate",
            code == 200
            and isinstance(body, dict)
            and body.get("ok") is True
            and str(rpath or "").startswith("reports/"),
            str(body)[:240],
        )
        check(
            "report disclaimer draft_not_claims",
            disclaimer == "draft_not_claims",
            f"disclaimer={disclaimer!r}",
        )

    # Soft export probe (may skip if optional deps missing)
    code, body = req(
        "POST",
        "/api/wiki/dossier/report/export",
        {
            "project_id": pid,
            "template": "briefing",
            "format": "md",
            "ensure_dossier": True,
        },
        timeout=90,
    )
    if code == 200:
        check("report export md", True)
    elif code in (403, 404, 409):
        check("report export md (optional)", False, f"status={code} (flag/deps)")
    else:
        # 501 / 400 when soft deps missing is acceptable for smoke
        check(
            "report export md (soft)",
            code in (200, 400, 501, 422),
            f"status={code} body={str(body)[:160]}",
        )

    print("\n── Hub UI checklist (manual) ──")
    print("1. Open Knowledge Hub → Wiki；确认活动项目为上述 project_id")
    print(f"   project_id = {pid}")
    print("2. 点「项目卷宗」→ Reader 显示 themes/project-*.md 与 section_revisions")
    print("3. 点「刷新卷宗」→ 无报错；S1 表含 salt_spray / VOC")
    print("4. Hub → Reports → 选 briefing → 生成 → 预览含 draft_not_claims")
    print("5. Chat 提问可命中卷宗，但 Claims 面板引用不得出现 source=wiki")
    print("6. DOE / 因子建议：不得出现 themes/project-* 或 reports/* 路径约束")
    print("Full checklist: docs/plans/2026-09-14-wiki-hub-dossier-handtest.md")

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("\nAll API smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
