#!/usr/bin/env python3
"""FormuMind 历史库 → 活跃库 迁移脚本
- 旧库: /root/FormuMind/data.bak/archived-db/formumind.db.backend-20260829
- 新库: /root/FormuMind/data/formumind.db (Docker 活跃)
- 策略: ATTACH + INSERT OR IGNORE，按外键依赖顺序，保留新库已有数据
"""
import sqlite3
import sys

OLD = "/root/FormuMind/data.bak/archived-db/formumind.db.backend-20260829"
NEW = "/root/FormuMind/data/formumind.db"

# 外键依赖顺序：先父后子
TABLES = [
    "projects",
    "materials",
    "campaigns",
    "experiments",
    "source_documents",
    "document_chunks",
    "doe_plans",
    "kb_entities",
    "kb_products",
    "kb_entity_links",
    "kb_mentions",
    "inferred_systems",
    "measurements",
    "formulation_versions",
    "experiment_attachments",
]
# task_outbox: 任务发件箱，历史任务消息对当前运行无意义，跳过

def common_columns(conn, table):
    """返回两库都存在的列名列表（按新库顺序）"""
    new_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return new_cols

def migrate():
    new = sqlite3.connect(NEW)
    new.execute("PRAGMA foreign_keys=OFF")
    new.execute(f'ATTACH DATABASE "{OLD}" AS old_db')
    old = sqlite3.connect(OLD)
    old.execute("PRAGMA foreign_keys=OFF")

    total_before = {}
    for t in TABLES:
        try:
            total_before[t] = new.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            print(f"  !! 新库无表 {t}，跳过")
            total_before[t] = -1
            continue

    for t in TABLES:
        if total_before[t] == -1:
            continue
        # 新库列
        ncols = [r[1] for r in new.execute(f"PRAGMA table_info({t})")]
        # 旧库列
        ocols = [r[1] for r in old.execute(f"PRAGMA table_info({t})")]
        common = [c for c in ncols if c in ocols]
        if not common:
            print(f"  !! {t}: 无公共列，跳过")
            continue
        col_sql = ", ".join(f'"{c}"' for c in common)
        try:
            n_old = old.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            sql = f'INSERT OR IGNORE INTO "{t}" ({col_sql}) SELECT {col_sql} FROM old_db."{t}"'
            cur = new.execute(sql)
            n_after = new.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            inserted = n_after - total_before[t]
            print(f"  {t}: 旧{n_old} → 插入{inserted} → 现有{n_after}")
        except sqlite3.IntegrityError as e:
            print(f"  !! {t}: 完整性错误 {e}")
        except sqlite3.OperationalError as e:
            print(f"  !! {t}: SQL错误 {e}\n     {sql[:200]}")

    new.commit()
    new.execute("VACUUM")
    new.close()
    old.close()
    print("\n迁移完成")

if __name__ == "__main__":
    migrate()
