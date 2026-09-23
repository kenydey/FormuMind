#!/usr/bin/env python3
"""STORM longform report smoke (live stack).

Enables storm flags, creates a project, kicks POST /api/wiki/storm/report,
waits for the async task, asserts GET + MD export, and checks disclaimer.

Usage:
  python3 scripts/wiki_storm_smoke.py
  FM_BASE=http://127.0.0.1:5173 python3 scripts/wiki_storm_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("FM_BASE", "http://127.0.0.1:5173").rstrip("/")
FAILURES: list[str] = []


def req(method: str, path: str, body: dict | None = None, *, timeout: float = 120):
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
                    return resp.status, json.loads(raw.decode()), dict(resp.headers)
                except json.JSONDecodeError:
                    return resp.status, raw.decode(errors="replace"), dict(resp.headers)
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode()), dict(e.headers)
        except Exception:
            return e.code, raw.decode(errors="replace"), dict(e.headers)
    except urllib.error.URLError as e:
        return 0, str(e), {}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  OK  {name}")
    else:
        print(f"FAIL  {name}: {detail}")
        FAILURES.append(f"{name}: {detail}")


def main() -> int:
    print(f"STORM longform smoke → {BASE}\n")

    code, body, _ = req("GET", "/health")
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
                "wiki_storm_report_enabled": True,
                "wiki_storm_parallel": False,
            }
        },
    )

    code, body, _ = req(
        "POST",
        "/api/projects",
        {
            "title": "STORM 冒烟项目",
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

    code, body, _ = req(
        "POST",
        "/api/wiki/storm/report",
        {
            "project_id": pid,
            "topic": "硅烷转化膜盐雾 720h",
            "max_sections": 4,
            "use_llm": False,
            "parallel": False,
            "ensure_dossier": True,
            "persist": True,
        },
    )
    task_id = body.get("task_id") if isinstance(body, dict) else None
    check(
        "storm enqueue 202",
        code == 202 and bool(task_id) and body.get("disclaimer") == "draft_not_claims",
        str(body)[:300],
    )
    if not task_id:
        return 1

    deadline = time.time() + 120
    terminal = None
    while time.time() < deadline:
        code, body, _ = req("GET", f"/api/tasks/{task_id}")
        if code == 200 and isinstance(body, dict):
            state = body.get("state")
            if state in ("completed", "failed", "cancelled"):
                terminal = body
                break
        time.sleep(0.5)

    check(
        "storm task completed",
        isinstance(terminal, dict) and terminal.get("state") == "completed",
        str(terminal)[:400] if terminal else "timeout",
    )

    code, body, _ = req("GET", f"/api/wiki/storm/report/{pid}")
    check(
        "get storm page",
        code == 200
        and isinstance(body, dict)
        and "storm" in (body.get("path") or "")
        and body.get("disclaimer") == "draft_not_claims"
        and "draft_not_claims" in (body.get("markdown") or ""),
        str(body)[:300] if isinstance(body, dict) else str(body)[:200],
    )

    code, raw, headers = req(
        "POST",
        "/api/wiki/storm/report/export",
        {"project_id": pid, "format": "md", "regenerate": False},
    )
    md_ok = code == 200 and isinstance(raw, (bytes, bytearray)) and b"draft_not_claims" in raw
    check(
        "export storm md",
        md_ok and "draft_not_claims" in (headers.get("X-FormuMind-Disclaimer") or headers.get("x-formumind-disclaimer") or ""),
        f"code={code} type={type(raw).__name__} hdr={headers.get('Content-Disposition')}",
    )

    # Flag gate: storm off → 409
    req(
        "POST",
        "/api/settings/env-flags",
        {"updates": {"wiki_storm_report_enabled": False}},
    )
    code, body, _ = req("POST", "/api/wiki/storm/report", {"project_id": pid})
    check(
        "storm flag 409",
        code == 409 and "wiki_storm_report_enabled" in str(body),
        str(body)[:200],
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All STORM smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
