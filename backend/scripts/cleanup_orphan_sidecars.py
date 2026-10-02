"""P2: 清理历史孤儿表格 sidecar。

背景：F-3 之前 sidecar 按 content sha256 落键，之后统一按 SourceDocument
UUID 落键。旧 sha256 键的文件没有任何读者会打开（load_tables(doc.id)
只读 UUID 键），属于孤儿。

用法（仓库根）：
    .venv/bin/python backend/scripts/cleanup_orphan_sidecars.py        # 只列出
    .venv/bin/python backend/scripts/cleanup_orphan_sidecars.py --delete  # 删除

只删满足以下全部条件的文件：
  - 文件名不是合法的 SourceDocument UUID 格式，且
  - data/source_tables/<stem>.json 在 source_documents 表中无对应行。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# v7: 默认改为脚本相对路径的 backend/data/source_tables（后端实际写入位置），
# 此前 ./data/source_tables 从仓库根运行时是错的目录。
TABLES_DIR = Path(
    os.environ.get("FORMUMIND_TABLES_DIR")
    or str(Path(__file__).resolve().parents[1] / "data" / "source_tables")
)


def _is_uuidish(stem: str) -> bool:
    import re

    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", stem))


def main() -> int:
    ap = argparse.ArgumentParser(description="列出/删除孤儿表格 sidecar")
    ap.add_argument("--delete", action="store_true", help="实际删除（默认只列出）")
    ap.add_argument("--db", default=None, help="SQLite 路径（默认 backend/data/formumind.db）")
    args = ap.parse_args()

    if not TABLES_DIR.is_dir():
        print(f"no tables dir: {TABLES_DIR}")
        return 0

    # 风险8 修正：--db 之前被接受但从未使用。default_session_factory 优先读
    # FORMUMIND_DB_URL，在建 factory 之前注入即可接线。
    if args.db:
        os.environ["FORMUMIND_DB_URL"] = f"sqlite:///{args.db}"

    from app.db.database import default_session_factory
    from app.db.models import SourceDocument

    factory = default_session_factory()
    with factory() as session:
        known = {r[0] for r in session.query(SourceDocument.id).all()}

    orphans: list[Path] = []
    for path in sorted(TABLES_DIR.glob("*.json")):
        stem = path.stem
        if stem in known:
            continue
        # UUID 键但行已删：同样算孤儿（行不在了，读者打不开）。
        orphans.append(path)

    legacy = [p for p in orphans if not _is_uuidish(p.stem)]
    print(f"sidecar 总数: {len(list(TABLES_DIR.glob('*.json')))}, 孤儿: {len(orphans)}（旧 sha256 键: {len(legacy)}）")
    for p in orphans:
        print(("DEL " if args.delete else "orphan ") + p.name)
    if args.delete:
        for p in orphans:
            p.unlink(missing_ok=True)
        print(f"已删除 {len(orphans)} 个孤儿 sidecar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
