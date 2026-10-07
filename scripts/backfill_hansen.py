#!/usr/bin/env python3
"""v20-2: Hansen 参数回填脚本（试点版）。

为 materials 表中缺 Hansen 参数的材料补全 δd/δp/δh。
策略（按优先级）：
1. 文献硬编码值（常见溶剂，准确可靠）
2. LLM 估计（标注为 estimated，需人工复核）
3. 跳过（无信息时保持 NULL，fail-open）

用法：
    # 试点 50 个
    backend/.venv/bin/python scripts/backfill_hansen.py --limit 50 --dry-run
    # 正式跑
    backend/.venv/bin/python scripts/backfill_hansen.py --limit 50

注意：LLM 估计值仅供参考，生产使用前需人工复核。
"""

from __future__ import annotations

import argparse
import sys

# 文献值（Hansen 官方手册，单位 MPa^0.5）
# 来源：Hansen, C.M. "Hansen Solubility Parameters: A User's Handbook" 2nd ed.
LITERATURE_HANSEN: dict[str, tuple[float, float, float]] = {
    # 常见溶剂
    "water": (15.5, 16.0, 42.3),
    "ethanol": (15.8, 8.8, 19.4),
    "isopropanol": (15.8, 6.1, 16.4),
    "n-butanol": (16.0, 5.7, 15.8),
    "acetone": (15.5, 10.4, 7.0),
    "mek": (16.0, 9.0, 5.1),  # methyl ethyl ketone
    "mibk": (15.3, 6.1, 4.1),  # methyl isobutyl ketone
    "toluene": (18.0, 1.4, 2.0),
    "xylene": (17.6, 1.0, 3.1),
    "ethyl acetate": (15.8, 5.3, 7.2),
    "butyl acetate": (15.8, 3.7, 6.3),
    "cyclohexanone": (17.8, 6.3, 5.1),
    "dmf": (17.4, 13.7, 11.3),  # N,N-dimethylformamide
    "nmp": (18.0, 12.3, 7.2),  # N-methyl-2-pyrrolidone
    "dmac": (16.8, 11.5, 10.2),  # dimethylacetamide
    "thf": (16.8, 5.7, 8.0),  # tetrahydrofuran
    "dichloromethane": (17.0, 7.3, 7.1),
    "chloroform": (17.8, 3.1, 5.7),
    # 常见助剂（近似值）
    "ethylene glycol": (17.0, 11.0, 26.0),
    "propylene glycol": (16.8, 9.4, 23.3),
    "glycerol": (17.4, 12.1, 29.3),
}


def _norm(name: str) -> str:
    return name.lower().strip()


def get_literature_value(name: str) -> tuple[float, float, float] | None:
    """查文献硬编码值（大小写不敏感，部分匹配）。"""
    n = _norm(name)
    # 精确匹配
    if n in LITERATURE_HANSEN:
        return LITERATURE_HANSEN[n]
    # 部分匹配（如 "Ethanol 99%" → "ethanol"）
    for key, val in LITERATURE_HANSEN.items():
        if key in n or n in key:
            return val
    return None


def estimate_via_llm(name: str, cas_no: str | None) -> tuple[float, float, float] | None:
    """用 LLM 估计 Hansen 参数（标注为估计值）。

    实际生产中应调用 DeepSeek/OpenAI。此处为脚本框架，
    返回 None 表示跳过（需人工介入或接 LLM）。
    """
    # TODO: 接入 LLM API
    # prompt 示例：
    # "给出 {name} (CAS: {cas_no}) 的 Hansen 溶解度参数 δd, δp, δh，
    #  单位 MPa^0.5，只返回三个数字，用逗号分隔。"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Hansen 参数回填（试点）")
    parser.add_argument("--limit", type=int, default=50, help="最多处理多少个材料")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    parser.add_argument("--use-llm", action="store_true", help="启用 LLM 估计（默认只用文献值）")
    args = parser.parse_args()

    # 延迟导入（避免脚本在非 backend 环境 import 失败）
    sys.path.insert(0, "backend")
    from app.db.database import default_session_factory

    # 查缺 Hansen 参数的材料
    # 直接用 SQL 查（MaterialStore.list_all 返回 ORM 对象）
    session_factory = default_session_factory()
    with session_factory() as session:
        from app.db.models import MaterialRow

        rows = (
            session.query(MaterialRow)
            .filter(MaterialRow.hansen_d.is_(None))
            .limit(args.limit)
            .all()
        )
        print(f"找到 {len(rows)} 个缺 Hansen 参数的材料（limit={args.limit}）")

        updated = 0
        skipped = 0
        for row in rows:
            lit = get_literature_value(row.name)
            if lit:
                d, p, h = lit
                source = "literature"
            elif args.use_llm:
                est = estimate_via_llm(row.name, row.cas_no)
                if est:
                    d, p, h = est
                    source = "llm_estimated"
                else:
                    print(f"  SKIP {row.name}（无文献值，LLM 未返回）")
                    skipped += 1
                    continue
            else:
                print(f"  SKIP {row.name}（无文献值，加 --use-llm 启用估计）")
                skipped += 1
                continue

            print(f"  {'[DRY]' if args.dry_run else '[SET]'} {row.name}: "
                  f"δd={d}, δp={p}, δh={h} ({source})")
            if not args.dry_run:
                row.hansen_d = d
                row.hansen_p = p
                row.hansen_h = h
                updated += 1

        if not args.dry_run:
            session.commit()
            print(f"\n已更新 {updated} 个，跳过 {skipped} 个")
        else:
            print(f"\n[DRY-RUN] 将更新 {len(rows) - skipped} 个，跳过 {skipped} 个")

    return 0


if __name__ == "__main__":
    sys.exit(main())
