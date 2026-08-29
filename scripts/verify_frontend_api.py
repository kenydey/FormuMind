#!/usr/bin/env python3
"""Frontend↔backend API cross-audit (v9+).

Extracts every `/api/...` string literal from frontend src (template literals
included; `${x}` segments and query strings normalised away) and cross-checks
against the backend OpenAPI schema. Reports:

  ① frontend calls with NO backend route  (dead / hanging calls)
  ② backend routes with NO frontend call (candidate dead routes)

Known limitations (all audited by hand at v9, see docs/plans/next-round-v9-frontend-audit.md):
- readApiError() description strings are counted as calls (false positive on ①)
- test assertion strings count as calls
- paths built by concatenating constants are missed (false negative on ②)
Treat output as a candidate list, not a verdict.

Usage:  backend/.venv/bin/python scripts/verify_frontend_api.py
        (run from repo root; requires the backend venv for `app.main`)
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(REPO, "frontend", "src")
BACKEND = os.path.join(REPO, "backend")

sys.path.insert(0, BACKEND)
from app.main import app  # noqa: E402

API_RE = re.compile(r"""['"`](/api/[^'"`\n]+)['"`]""")


def norm(p: str) -> str:
    """/api/experiments/${id}/attachments -> /api/experiments/{}/attachments
    query strings stripped: /api/doe?design=x -> /api/doe"""
    p = p.split("?", 1)[0]
    return re.sub(r"\$\{[^}]*\}", "{}", p)


def main() -> int:
    frontend_calls: Counter[str] = Counter()
    for dirpath, _, files in os.walk(FRONTEND):
        if "node_modules" in dirpath:
            continue
        for fn in files:
            if not fn.endswith((".ts", ".tsx", ".js", ".jsx")):
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                for line in f:
                    for m in API_RE.finditer(line):
                        frontend_calls[norm(m.group(1))] += 1

    backend_paths = {
        re.sub(r"\{[^}]*\}", "{}", path) for path in app.openapi()["paths"]
    }

    print(f"前端 API 调用（去重）: {len(frontend_calls)}")
    print(f"后端 OpenAPI 路由: {len(backend_paths)}\n")

    print("=== ① 前端调用但后端无此路由（死调用/隐患）===")
    dead = sorted(c for c in frontend_calls if c not in backend_paths)
    for c in dead:
        print(f"  {c}  (引用 {frontend_calls[c]} 处)")
    if not dead:
        print("  无 ✓")

    print("\n=== ② 后端路由但前端从不调用（候选死路由）===")
    unused = sorted(p for p in backend_paths if p not in set(frontend_calls))
    for p in unused:
        print(f"  {p}")
    if not unused:
        print("  无 ✓")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
