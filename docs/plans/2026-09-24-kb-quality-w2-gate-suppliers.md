# KB 质量 W2：topicality 可强制闸 + 供应商可编辑

> 状态：**已实现**（2026-09-24）  
> 前置：W1 shadow 可观测 + golden MRR（#141）  
> 约束：**默认仍 shadow**（`kb_relevance_shadow=True`）；不默认开 `auto_loop`；不扩 Wiki/Neo4j

## 0. 本批做 / 不做

| 做 | 不做 |
|----|------|
| W2：`kb_relevance_shadow=False` 时按 topicality 真闸（阈值仍 `kb_ingest_min_relevance`） | 默认 flip shadow |
| W2：env-flags 暴露开关 + 文档指向 `/relevance-shadow/stats` | 改 `topic_gate` 词表 / Claims / DOE |
| W2′：MaterialsPanel 供应商表可增删改，保存双写归一化表 | 供应商主数据独立 CRUD UI |

## 1. 行为

### Topicality

| `kb_relevance_shadow` | 行为 |
|----------------------|------|
| `True`（默认） | 只记 shadow JSONL/stats；仍走名次代理 `low_relevance` |
| `False` | 有 query 关键词时，`topicality < threshold` → `skip/low_topicality`；**不再**走名次代理闸 |

无 query 关键词时 enforce 跳过（避免全拒）。

### Suppliers

- 新建/编辑材料模态始终显示供应商编辑区
- 空名行保存时过滤；`upsert` 仍经 `sync_material_suppliers_from_json` 双写

## 2. 文件

| 文件 | 改动 |
|------|------|
| `backend/app/services/kb_ingest.py` | enforce 分支 |
| `backend/app/config.py` / `env_flags.py` / `api/kb.py` | 注释 + flag |
| `frontend/src/components/MaterialsPanel.tsx` | 可编辑供应商 |
| `backend/tests/test_topicality_enforce.py` | 单测 |
| `backend/scripts/ci_golden_gate.sh` | CI 无 `.venv` 可跑 |

## 3. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_topicality_enforce.py \
  tests/test_openalex_arms.py -k 'shadow or topicality' \
  tests/test_relevance_shadow_stats.py
bash scripts/ci_golden_gate.sh
```
