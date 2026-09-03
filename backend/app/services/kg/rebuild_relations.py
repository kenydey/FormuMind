"""CLI: 重跑 KG 关系提取（只关系，不重跑实体提及）。

用法：
  python -m app.services.kg.rebuild_relations --all
  python -m app.services.kg.rebuild_relations --source <source_id> [--source ...]
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="重跑 KG 关系提取（只关系，不重跑实体提及）"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="所有含 mentions 的 source")
    group.add_argument(
        "--source", action="append", dest="sources", metavar="ID",
        help="指定 source_id（可多次）",
    )
    args = parser.parse_args(argv)

    from .entity_linker import rebuild_relations

    result = rebuild_relations(source_ids=args.sources) if args.sources else rebuild_relations()
    print(
        f"结果: rebuilt_sources={result['rebuilt_sources']} "
        f"relations_upserted={result['relations_upserted']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
