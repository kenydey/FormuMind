#!/usr/bin/env python3
"""Grayscale Wiki + KG main-track live smoke.

Enables dossier/report flags, creates a project, ensures/refreshes dossier,
generates briefing MD, and probes KG feedback stats.

Usage:
  python3 scripts/grayscale_kg_maintrack_smoke.py
  FM_BASE=http://127.0.0.1:5173 python3 scripts/grayscale_kg_maintrack_smoke.py

Companion: docs/plans/2026-09-22-grayscale-kg-maintrack.md
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
    print(f"Grayscale + KG main-track smoke → {BASE}\n")

    code, body = req("GET", "/health")
    check(
        "health",
        code == 200 and isinstance(body, dict) and body.get("status") in ("ok", "degraded"),
        str(body)[:200],
    )

    code, body = req(
        "POST",
        "/api/settings/env-flags",
        {
            "updates": {
                "wiki_enabled": True,
                "wiki_project_dossier_enabled": True,
                "wiki_dossier_report_enabled": True,
                "wiki_chat_save_draft": True,
                "wiki_doe_constraints": True,
                "wiki_dossier_auto_patch": False,
            }
        },
    )
    check(
        "enable grayscale wiki flags",
        code == 200 and isinstance(body, dict),
        str(body)[:200],
    )

    code, body = req(
        "POST",
        "/api/projects",
        {
            "title": "主航道灰度·硅烷",
            "requirement": {
                "domain": "anticorrosion_coating",
                "substrate": "carbon_steel",
                "salt_spray_hours": 720,
                "voc_limit_gpl": 350,
                "notes": "grayscale_kg_maintrack_smoke",
            },
        },
    )
    pid = body.get("id") if isinstance(body, dict) else None
    check("create project", code in (200, 201) and bool(pid), str(body)[:240])
    if not pid:
        return 1

    code, body = req(
        "POST", "/api/wiki/dossier/ensure", {"project_id": pid, "vertical": "silane"}
    )
    check(
        "dossier ensure",
        code == 200 and isinstance(body, dict) and body.get("ok") is True,
        str(body)[:240],
    )

    code, body = req("POST", "/api/wiki/dossier/refresh", {"project_id": pid})
    check("dossier refresh", code == 200 and isinstance(body, dict), str(body)[:200])

    code, pack = req("GET", f"/api/wiki/dossier/{pid}/pack")
    pack_ok = code == 200 and isinstance(pack, dict)
    check("dossier pack", pack_ok, str(pack)[:200])
    if pack_ok:
        rows = (pack.get("requirements") or {}).get("rows") or []
        check("S1 requirements non-empty", bool(rows), str(pack.get("flags")))

    code, body = req(
        "POST",
        "/api/wiki/dossier/report",
        {
            "project_id": pid,
            "template": "briefing",
            "ensure_dossier": True,
            "persist": True,
            "use_llm": False,
        },
    )
    check(
        "briefing generate",
        code == 200
        and isinstance(body, dict)
        and body.get("ok") is True
        and body.get("disclaimer") == "draft_not_claims",
        str(body)[:240],
    )

    code, body = req(
        "POST",
        "/api/wiki/dossier/report/export",
        {
            "project_id": pid,
            "template": "briefing",
            "format": "md",
            "ensure_dossier": True,
            "use_llm": False,
        },
    )
    if code == 200:
        raw = body if isinstance(body, (bytes, bytearray)) else str(body).encode()
        text = raw.decode("utf-8", errors="replace")
        check("export MD", len(raw) > 40 and ("720" in text or "简报" in text), text[:100])
    else:
        check("export MD", False, f"status={code}")

    code, body = req("GET", "/api/kg/feedback/stats")
    check(
        "kg feedback stats",
        code == 200
        and isinstance(body, dict)
        and "measured_material" in body
        and "measured_domain" in body,
        str(body)[:240],
    )
    if isinstance(body, dict):
        print(
            f"  ..  measured_material={body.get('measured_material')} "
            f"measured_domain={body.get('measured_domain')}"
        )

    # G4-ish: Chat 存草稿入口可达（旗标默认开）
    code, flags = req("GET", "/api/settings/env-flags")
    flag_map = {}
    if code == 200 and isinstance(flags, dict):
        flag_map = {f.get("attr"): f for f in (flags.get("flags") or [])}
    for attr, want in (
        ("wiki_chat_save_draft", True),
        ("wiki_storm_report_enabled", True),
        ("wiki_dossier_auto_patch", False),
    ):
        f = flag_map.get(attr) or {}
        check(
            f"flag default {attr}={want}",
            bool(f.get("value")) is want or f.get("default") is want or f.get("value") == want,
            str(f)[:160],
        )

    # STORM 旗标开时 API 接受（离线草稿，不强制跑完 LLM）
    code, body = req(
        "POST",
        "/api/wiki/storm/report",
        {
            "project_id": pid,
            "topic": "灰度冒烟 STORM",
            "max_sections": 3,
            "use_llm": False,
        },
    )
    check(
        "storm report accepted",
        code in (200, 202) and isinstance(body, dict) and bool(body.get("task_id") or body.get("ok")),
        str(body)[:240],
    )

    # KG relations rebuild accepts with limit (async)
    code, body = req("POST", "/api/kg/relations/rebuild", {"limit": 5})
    check(
        "kg relations rebuild accepted",
        code in (200, 202) and isinstance(body, dict) and bool(body.get("task_id")),
        str(body)[:240],
    )

    print(f"\nproject_id = {pid}")
    print("Next: Hub Wiki 卷宗 / Reports STORM / Workbench sync → KG 回流提示")
    print("Checklist: docs/plans/2026-09-22-grayscale-kg-maintrack.md")

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("\nGrayscale + KG main-track smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
