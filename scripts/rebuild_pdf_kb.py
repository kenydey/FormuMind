"""Re-parse the stored PDF sources with local OCR now enabled.

Why a delete is required: kb_ingest dedupes by origin_url (kb_ingest.py:321 and
:562), so re-submitting an already-stored document returns `skipped` and never
re-fetches or re-parses it. Recovering the pages that were dropped while OCR was
inactive therefore means removing the stale rows so the same evidence is treated
as new. A consistent DB backup was taken before this runs (see
/tmp/formumind_backup_path.txt).

Scope: literature + patent rows of project 81c0dfc1 (35 docs). The 7 `web` rows
are HTML pages with no OCR-sensitive content and are left untouched.
"""
from __future__ import annotations

import json
import sqlite3
import sys

DB = "/root/FormuMind/data/formumind.db"
PROJECT_ID = "81c0dfc1-1984-455f-a5d4-00df1a08aa37"

con = sqlite3.connect(DB)
con.execute("PRAGMA foreign_keys=ON")

# Recompute the id list from the live DB: earlier runs stored different UUIDs, so
# a file written by a previous invocation would point at rows that no longer exist.
source_ids = [
    r[0]
    for r in con.execute(
        "SELECT id FROM source_documents WHERE source_kind IN ('literature','patent')"
    ).fetchall()
]
evidence: list[dict] = json.load(open("/tmp/pdf_evidence.json"))
assert len(evidence) == 35, len(evidence)
assert source_ids, "没有找到 literature/patent 行，无需重解析"
print(f"待重解析 PDF 行: {len(source_ids)} 篇 | 证据表: {len(evidence)} 条")

qmarks = ",".join("?" * len(source_ids))

# --- wiki_pages.source_ids is a JSON array of source ids; drop the ones that are
# --- about to disappear so re-ingest does not leave dangling citation anchors.
fixed_pages = 0
for pid, raw in con.execute("SELECT id, source_ids FROM wiki_pages").fetchall():
    if not raw:
        continue
    try:
        ids = json.loads(raw)
    except Exception:
        continue
    if not isinstance(ids, list):
        continue
    kept = [i for i in ids if i not in set(source_ids)]
    if len(kept) != len(ids):
        con.execute("UPDATE wiki_pages SET source_ids=? WHERE id=?", (json.dumps(kept), pid))
        fixed_pages += 1

n_chunks = con.execute(
    f"SELECT COUNT(*) FROM document_chunks WHERE source_id IN ({qmarks})", source_ids
).fetchone()[0]
n_mentions = con.execute(
    f"SELECT COUNT(*) FROM kb_mentions WHERE source_id IN ({qmarks})", source_ids
).fetchone()[0]
n_docs = con.execute(
    f"SELECT COUNT(*) FROM source_documents WHERE id IN ({qmarks})", source_ids
).fetchone()[0]

con.execute(f"DELETE FROM document_chunks WHERE source_id IN ({qmarks})", source_ids)
con.execute(f"DELETE FROM kb_mentions WHERE source_id IN ({qmarks})", source_ids)
con.execute(f"DELETE FROM source_documents WHERE id IN ({qmarks})", source_ids)
con.commit()

print(f"删除: {n_docs} 篇 / {n_chunks} chunks / {n_mentions} kb_mentions "
      f"| wiki 引用清理 {fixed_pages} 页")

remaining = con.execute(
    "SELECT source_kind, COUNT(*) FROM source_documents GROUP BY source_kind"
).fetchall()
print("删除后剩余 source_documents:", remaining, "| chunks=",
      con.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0])
con.close()

# --- dispatch the re-ingest through the normal Celery path -------------------
sys.path.insert(0, "/root/FormuMind/backend")
from app.worker.celery_app import celery_app  # noqa: E402

payload = {"evidence": evidence, "project_id": PROJECT_ID}
res = celery_app.send_task("formumind.kb_ingest", args=[payload])
try:
    from app.services import task_manager

    task_manager.register_celery_task(res.id, "kb_ingest")
except Exception as exc:  # tracking is a nicety, not a precondition
    print("task_manager 注册跳过:", exc)

print("已派发 formumind.kb_ingest task_id =", res.id)
print("evidence 篇数 =", len(evidence))
