# KB 质量 W3：知识库软归档

> 状态：**已实现**（2026-09-24）  
> 前置：W1 shadow/golden · W2 topicality flag + suppliers（#141）  
> 约束：不默认 flip `kb_relevance_shadow` / `auto_loop`；不扩 Wiki/Neo4j；不改 Claims/DOE；**不做** TTL/冷存储

## 0. 本批做 / 不做

| 做 | 不做 |
|----|------|
| `source_documents.archived` + soft-column 守护 + alembic 0029 | 切块级 archive 列 |
| `POST /api/kb/sources/{id}/archive`；列表默认隐藏；`include_archived` | 替换硬删除 |
| `all_chunks` / hybrid / keyword 默认排除已归档源 | 热/冷分层、自动 TTL 物理删 |
| Hub「归档 / 恢复」+「显示已归档」；硬删降为次级「永久删除」 | W3′ loop retry / auto_adopt |

## 1. 行为

| 动作 | 列表 | 检索 | 切块行 | 配额 |
|------|------|------|--------|------|
| 归档 | 默认隐藏 | 排除 | 保留 | 仍计入 |
| 恢复 | 回显 | 回显 | 不变 | 不变 |
| 永久删除 | 移除 | 移除 | 级联删 | −1 |

## 2. 文件

| 文件 | 改动 |
|------|------|
| `backend/app/db/models.py` / `0029_*.py` / `database.py` | 列 + 迁移 + 软扩 |
| `backend/app/db/source_store.py` / `chunk_store.py` | set_archived / list+all_chunks 过滤 |
| `backend/app/api/kb.py` | archive API + `archived` 字段 |
| `frontend/.../HubMaterialsPane*` / `api.ts` | UI + 客户端 |
| `backend/tests/test_kb_source_archive.py` | 单测 |

## 3. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_kb_source_archive.py \
  tests/test_kb_source_delete.py
cd frontend && npx vitest run \
  src/components/knowledge-hub/HubMaterialsPane.archive.test.tsx
```
