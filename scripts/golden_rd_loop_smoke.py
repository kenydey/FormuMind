#!/usr/bin/env python3
"""Golden R&D loop smoke — 真实项目数据填满卷宗五表 + Report + S4 草稿运营化.

Requires a live FormuMind stack (Vite → API) with wiki/dossier/report flags enabled.
Optionally enables ``wiki_chat_save_draft`` for the S4 draft step.

Usage:
  python3 scripts/golden_rd_loop_smoke.py
  FM_BASE=http://127.0.0.1:5173 python3 scripts/golden_rd_loop_smoke.py
  FM_ENABLE_DRAFT=0 python3 scripts/golden_rd_loop_smoke.py   # skip S4 draft step

Companion: docs/plans/2026-09-14-wiki-hub-dossier-handtest.md §4
            docs/plans/2026-09-21-s4-draft-ops-handtest.md
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("FM_BASE", "http://127.0.0.1:5173").rstrip("/")
ENABLE_DRAFT = os.environ.get("FM_ENABLE_DRAFT", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
FAILURES: list[str] = []


def req(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    timeout: float = 90,
) -> tuple[int, dict | str | bytes]:
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


def _golden_workspace() -> dict:
    return {
        "sources": [
            {
                "identifier": "golden-lit-smoke-1",
                "title": "硅烷偶联剂水解与盐雾性能综述",
                "snippet": "CAS 2530-83-8 · 金样文献",
                "source": "literature",
                "relevance": 0.9,
            }
        ],
        "doe_plan": {
            "plan_id": "doe-golden-smoke",
            "design": "lhs",
            "notes": "金样 DOE smoke",
            "factors": [{"name": "pH", "low": 3.5, "high": 5.5, "unit": ""}],
            "runs": [
                {
                    "run_id": 1,
                    "coded": {"pH": 0.0},
                    "natural": {"pH": 4.5},
                }
            ],
        },
        "leaderboard": [
            {
                "name": "基准浴液",
                "domain": "anticorrosion_coating",
                "ingredients": [
                    {
                        "name": "GPTMS",
                        "role": "silane",
                        "weight_pct": 2.0,
                        "cas_no": "2530-83-8",
                    },
                    {"name": "水", "role": "solvent", "weight_pct": 98.0},
                ],
                "score": 0.82,
                "predicted": {"salt_spray_hours": 650.0},
            }
        ],
        "rmse_history": [
            {"rmse": 0.21, "round": 1},
            {"rmse": 0.11, "round": 2, "converged": False},
        ],
        # S5 回退：ELN 不可达时 dossier 仍可从 workspace.measured 填表
        "measured": {"salt_spray_hours": 680.0},
    }


def main() -> int:
    print(f"Golden R&D loop smoke → {BASE}\n")

    code, body = req("GET", "/health")
    check(
        "health",
        code == 200 and isinstance(body, dict) and body.get("status") in ("ok", "degraded"),
        str(body)[:200],
    )

    # Enable grayscale + S4 draft for this smoke run (staging-friendly).
    code, body = req(
        "POST",
        "/api/settings/env-flags",
        {
            "updates": {
                "wiki_enabled": True,
                "wiki_project_dossier_enabled": True,
                "wiki_dossier_report_enabled": True,
                "wiki_doe_constraints": True,
                "wiki_chat_blend": True,
                "wiki_dossier_auto_patch": False,
                "wiki_dossier_llm_narrative": False,
                "wiki_chat_save_draft": ENABLE_DRAFT,
            }
        },
    )
    check(
        "enable wiki/dossier/report flags",
        code == 200 and isinstance(body, dict) and "wiki_enabled" in (body.get("updated") or []),
        str(body)[:240],
    )

    code, body = req(
        "POST",
        "/api/projects",
        {
            "title": "金样硅烷转化膜",
            "requirement": {
                "domain": "anticorrosion_coating",
                "substrate": "carbon_steel",
                "salt_spray_hours": 720,
                "voc_limit_gpl": 350,
                "notes": "golden_rd_loop_smoke",
            },
        },
    )
    pid = body.get("id") if isinstance(body, dict) else None
    check("create project", code in (200, 201) and bool(pid), str(body)[:240])
    if not pid:
        return 1

    # 文献入库：ingest-evidence 支持 patent/surechembl（literature 标识符会失败）
    code, body = req(
        "POST",
        "/api/kb/ingest-evidence",
        {
            "identifier": "CN-104789083-B",
            "title": "硅烷转化膜专利样例",
            "snippet": "硅烷偶联剂水解缩合与盐雾性能。CAS 2530-83-8。",
            "source": "surechembl",
            "url": "https://patents.google.com/patent/CN104789083B",
            "project_id": pid,
            "relevance": 0.92,
        },
        timeout=120,
    )
    ingest_ok = (
        code == 200
        and isinstance(body, dict)
        and body.get("ok") is True
        and body.get("status") in ("indexed", "skipped", "queued")
    )
    check("ingest patent evidence (S2)", ingest_ok, f"status={code} {str(body)[:200]}")

    code, body = req(
        "PUT",
        f"/api/projects/{pid}",
        {"workspace": _golden_workspace()},
    )
    check("seed workspace (DOE/leaderboard/loop)", code == 200, str(body)[:200])

    code, body = req(
        "POST",
        "/api/experiments",
        {
            "retrain": False,
            "records": [
                {
                    "domain": "anticorrosion_coating",
                    "project_id": pid,
                    "factors": {"pH": 4.5},
                    "measured": {"salt_spray_hours": 680},
                    "source": "lab",
                    "label": "G-01",
                }
            ],
        },
    )
    exp_ok = code == 200 and isinstance(body, dict)
    check("submit lab experiment (best-effort ELN)", exp_ok, str(body)[:200])

    code, body = req(
        "POST",
        "/api/wiki/dossier/ensure",
        {"project_id": pid, "vertical": "silane"},
    )
    check(
        "dossier ensure",
        code == 200 and isinstance(body, dict) and body.get("ok") is True,
        str(body)[:240],
    )

    code, body = req("POST", "/api/wiki/dossier/refresh", {"project_id": pid})
    check(
        "dossier refresh",
        code == 200 and isinstance(body, dict) and body.get("ok") is not False,
        str(body)[:200],
    )

    code, pack = req("GET", f"/api/wiki/dossier/{pid}/pack")
    pack_ok = code == 200 and isinstance(pack, dict)
    check("dossier pack", pack_ok, str(pack)[:200])
    if pack_ok:
        flags = pack.get("flags") or {}
        check("S1 requirements non-empty", bool((pack.get("requirements") or {}).get("rows")), "")
        check("S2 literature non-empty", not flags.get("empty_literature"), str(flags))
        check("S3 formula non-empty", bool((pack.get("formula") or {}).get("rows")), "")
        check("S4 DOE non-empty", not flags.get("empty_doe"), str(flags))
        check("S5 lab non-empty", not flags.get("empty_lab"), str(flags))
        check("S6 loop non-empty", not flags.get("empty_loop"), str(flags))

    if ENABLE_DRAFT:
        code, body = req(
            "POST",
            "/api/wiki/drafts/save",
            {
                "project_id": pid,
                "question": "硅烷浴 pH 窗口对盐雾的影响？",
                "answer_markdown": (
                    "## 草稿结论（金样 smoke）\n\n"
                    "建议 pH 4.2–4.8；**draft_not_claims** — 需人工审阅后再引用。\n"
                ),
                "title": "硅烷 pH 窗口笔记",
                "origin": "chat",
            },
        )
        draft_path = body.get("path") if isinstance(body, dict) else None
        check(
            "S4 save chat draft",
            code == 200
            and isinstance(body, dict)
            and str(draft_path or "").startswith("queries/project-"),
            str(body)[:240],
        )
        code, body = req("GET", f"/api/wiki/pages?limit=50&project_id={pid}")
        pages = (body.get("pages") if isinstance(body, dict) else None) or []
        qpaths = [
            p.get("path")
            for p in pages
            if isinstance(p, dict) and str(p.get("path") or "").startswith("queries/")
        ]
        check("S4 draft listed in project wiki", len(qpaths) >= 1, f"paths={qpaths[:3]}")

        code, body = req("POST", "/api/wiki/dossier/refresh", {"project_id": pid})
        code2, dossier = req("GET", f"/api/wiki/dossier/{pid}")
        md = dossier.get("markdown") if isinstance(dossier, dict) else ""
        check(
            "S8 mentions query drafts after refresh",
            code2 == 200 and isinstance(md, str) and "queries/" in md,
            (md or "")[md.find("S8"): md.find("S8") + 200] if md else "",
        )

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
        check("export MD", len(raw) > 80 and ("720" in text or "salt_spray" in text), text[:120])
    else:
        check("export MD", False, f"status={code}")

    print(f"\nproject_id = {pid}")
    print("Hub: Wiki → 项目卷宗 → 刷新；Reports → briefing → 导出 MD")
    if ENABLE_DRAFT:
        print("Chat: 助手回答 →「存为 Wiki 草稿」→ queries/ 待审")

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("\nGolden R&D loop smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
