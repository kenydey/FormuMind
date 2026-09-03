"""FormuMind 管理 CLI。

用法：
  python -m app.cli inferred-systems hot [--threshold N]
  python -m app.cli inferred-systems promote --key <normalized_key>
"""

from __future__ import annotations

import argparse
import sys


def _cmd_inferred_hot(args: argparse.Namespace) -> int:
    from app.db.inferred_system_store import get_inferred_system_store

    store = get_inferred_system_store()
    hot = store.hot(threshold=args.threshold)
    if not hot:
        print(f"无 hit_count ≥ {args.threshold} 的沉淀约束（沉淀库为空或热度不足）")
        return 0

    print(f"共 {len(hot)} 条升级候选（hit_count ≥ {args.threshold}）：\n")
    for i, h in enumerate(hot, 1):
        print(f"[{i}] {h['product_type']}")
        print(f"    体系: {h['system_name']}")
        print(f"    热度: {h['hit_count']}  |  置信度: {h['confidence']}")
        print(f"    溯源: {h['source_requirement_text'] or '(无)'}")
        print(f"    固化: python -m app.cli inferred-systems promote --key {h['normalized_key']}")
        print()
    print("人工 review 后：1) 把候选写入 app/domain/formulation_systems.py 的 FORMULATION_SYSTEMS；")
    print("               2) 用上面的 promote 命令标记为 promoted。")
    return 0


def _cmd_inferred_promote(args: argparse.Namespace) -> int:
    from app.db.inferred_system_store import get_inferred_system_store

    store = get_inferred_system_store()
    ok = store.mark_promoted(args.key)
    if ok:
        print(f"已标记 {args.key} 为 promoted")
        return 0
    print(f"未找到 normalized_key={args.key} 的沉淀条目")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FormuMind 管理 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inf = sub.add_parser("inferred-systems", help="沉淀约束知识库管理")
    inf_sub = p_inf.add_subparsers(dest="subcommand", required=True)

    p_hot = inf_sub.add_parser("hot", help="列出高频升级候选")
    p_hot.add_argument("--threshold", type=int, default=5, help="hit_count 阈值（默认 5）")
    p_hot.set_defaults(func=_cmd_inferred_hot)

    p_promote = inf_sub.add_parser("promote", help="标记某条目为 promoted（review 后）")
    p_promote.add_argument("--key", required=True, help="normalized_key")
    p_promote.set_defaults(func=_cmd_inferred_promote)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
