#!/usr/bin/env python3
"""D1 (2026-09-04): document_chunks 语言回填 + 乱码/噪音清单(双语分流前置)。

分类: CJK 占比 ≥25% → zh; 含控制字符/替换符/二进制乱码 → lang=NULL 进
乱码清单(不参与检索语义); 其余 → en。营销/导航噪音页(电话/微信/点击
索取/ICP 等)zh 判定后另记 noisy 清单(供独立清洗工单, 本脚本不删)。

用法: .venv/bin/python scripts/backfill_chunk_lang.py [--dry-run]
输出: 分布统计 + /tmp/chunk_lang_report.json(乱码/噪音清单)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
DB = Path("/root/FormuMind/data/formumind.db")

_CJK = re.compile(r"[\u4e00-\u9fff]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_REPL = "\ufffd"
_NOISE = re.compile(
    r"电话|微信|QQ|邮箱|索取样品|点击|版权所有|ICP备|首页|产品中心|联系我们|"
    r"地址[:：]|手机[:：]|技术咨询[:：]|返回列表|上一篇|下一篇",
    re.I,
)


def classify(text: str) -> tuple[str | None, bool, bool]:
    """→ (lang|None, 乱码?, 噪音?)"""
    if not text:
        return None, False, False
    garbled = bool(_CTRL.search(text)) or _REPL in text or "\x00" in text
    if garbled:
        return None, True, False
    cjk = len(_CJK.findall(text))
    ratio = cjk / max(1, len(text))
    if ratio >= 0.25:
        return "zh", False, bool(_NOISE.search(text))
    return "en", False, False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    rows = cur.execute("SELECT id, text, lang FROM document_chunks").fetchall()

    counts = {"zh": 0, "en": 0, "garbled": 0, "changed": 0}
    garbled_ids, noisy_ids = [], []
    updates = []
    for r in rows:
        lang, garbled, noisy = classify(r["text"] or "")
        if garbled:
            counts["garbled"] += 1
            garbled_ids.append(r["id"])
            continue
        if lang is None:
            continue
        counts[lang] += 1
        if noisy:
            noisy_ids.append({"id": r["id"], "lang": lang})
        if (r["lang"] or "") != lang:
            counts["changed"] += 1
            updates.append((lang, r["id"]))

    print(f"总 {len(rows)} | zh {counts['zh']} | en {counts['en']} | "
          f"乱码 {counts['garbled']} | 需更新 {counts['changed']} | 噪音标记 {len(noisy_ids)}")
    if args.dry_run:
        print("dry-run, 未写库")
    else:
        cur.executemany("UPDATE document_chunks SET lang=? WHERE id=?", updates)
        con.commit()
        print(f"已更新 {len(updates)} 行 lang")
    json.dump(
        {"garbled": garbled_ids, "noisy": noisy_ids[:500], "counts": counts},
        open("/tmp/chunk_lang_report.json", "w"), ensure_ascii=False, indent=2,
    )
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
