#!/usr/bin/env python
"""P2-25 (v27): KB coverage 计数器对账。

_bump_kb_coverage 双写（进程内 _KB_COVERAGE + DB kb_coverage_counters）；
DB 写失败时两者静默分叉。本脚本对比 DB 计数器与实测值，漂移时告警。

用法：
    cd backend && .venv/bin/python ../scripts/check_kb_coverage.py [--db PATH] [--fix]

--fix: 用实测值覆盖 DB 计数器（需人工确认漂移原因后使用）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="覆盖默认 DB 路径")
    ap.add_argument("--fix", action="store_true", help="用实测值修复 DB 计数器")
    args = ap.parse_args()

    if args.db:
        import os

        os.environ["FORMUMIND_DB_URL"] = f"sqlite:///{args.db}"

    from app.db.chunk_store import get_chunk_store
    from app.services.kb_index import get_kb_coverage_stats

    stats = get_kb_coverage_stats()
    total, embedded = get_chunk_store().counts()

    drift = []
    for key, actual in (("kb_chunks_total", total), ("kb_chunks_embedded", embedded)):
        counter = stats.get(key)
        # 计数器可能从未初始化（None）—— 视为 0 对比
        counter = int(counter or 0)
        if counter != actual:
            drift.append((key, counter, actual))

    print(f"counters: total={stats.get('kb_chunks_total')} embedded={stats.get('kb_chunks_embedded')}")
    print(f"actual:   total={total} embedded={embedded}")
    if not drift:
        print("OK: 无漂移")
        return 0

    print("DRIFT:")
    for key, counter, actual in drift:
        print(f"  {key}: counter={counter} actual={actual} (diff={actual - counter})")

    if args.fix:
        from sqlalchemy import text as _text

        from app.db.session_utils import commit_session
        from app.services.kb_index import _coverage_session_factory

        with commit_session(_coverage_session_factory()) as session:
            for key, _, actual in drift:
                session.execute(
                    _text(
                        "INSERT INTO kb_coverage_counters (key, value, updated_at) "
                        "VALUES (:k, :v, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(key) DO UPDATE SET value = :v, "
                        "updated_at = CURRENT_TIMESTAMP"
                    ),
                    {"k": key, "v": int(actual)},
                )
        print("已用实测值修复 DB 计数器")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
