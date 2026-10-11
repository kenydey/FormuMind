#!/usr/bin/env python3
"""EPO OPS 全文回退生产验证（2026-10-11）。

用法：
  1. 经设置界面/API 配置 EPO consumer key/secret
     （或 export FORMUMIND_EPO_CONSUMER_KEY / FORMUMIND_EPO_CONSUMER_SECRET）
  2. backend/.venv/bin/python scripts/verify_epo_fulltext.py EP1234567

验证内容：
- OPS OAuth token 获取
- bibliographic search → docdb → fulltext XML 全链路
- 输出全文字符数、耗时
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main() -> int:
    patent_id = sys.argv[1] if len(sys.argv) > 1 else "EP1234567"
    # 仅 EP 专利走 EPO 回退
    if not patent_id.upper().startswith("EP"):
        print(f"SKIP: {patent_id} 非 EP 专利，EPO 回退仅适用于 EP")
        return 2

    from app.config import get_settings
    s = get_settings()
    if not s.epo_consumer_key or not s.epo_consumer_secret:
        print("SKIP: EPO 凭证未配置（设置界面/API 或环境变量）")
        print("  FORMUMIND_EPO_CONSUMER_KEY / FORMUMIND_EPO_CONSUMER_SECRET")
        return 2

    from app.services import epo_ops
    print(f"验证 EP 专利全文回退: {patent_id}")
    t0 = time.time()
    try:
        text = epo_ops.fetch_fulltext(
            patent_id, key=s.epo_consumer_key, secret=s.epo_consumer_secret,
            timeout=60.0,
        )
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    dt = time.time() - t0
    if not text or not text.strip():
        print("FAIL: 全文为空")
        return 1
    print(f"OK: {len(text)} 字符，耗时 {dt:.1f}s")
    # 抽查前 200 字符
    print(f"  预览: {text[:200]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
