# 门禁丢弃计数进探针 / Hub（独立方案 + MVP）

> 状态：**已实现**（2026-09-17）  
> 前置：#111 检索门禁、#112 入库门禁、#113 Modal 纵向布局已合入 main  
> 缺口：规则已挡 top-k / 写库，但运营侧看不见「拦了多少 / 什么原因」  
> 约束：进程内计数；跟随 `content_filter_enabled`；不引入 DB 表 / Prometheus / 全库 scrub

## 0. 结论摘要

| 本 MVP 做 | 不做 |
|-----------|------|
| `kb_retrieval_gate` 进程内累计：retrieval / ingest × reason | 持久化表、跨进程聚合 |
| `POST /api/kb/query-test` 返回本轮 `gate_drops`（delta）+ `gate_drops_total` | 改 hybrid 公式 |
| `GET /api/kb/stats` 附带 `quality_gate_drops` 终身计数 | 默认 LLM judge |
| Hub 探针面板展示本轮丢弃；依赖页 KB 卡展示累计 | 大看板重构 |

## 1. 决策锁定

1. **单点记账**：只在 `gate_chunk_indices` / `gate_ingest_rows`（+ `index_source` 早拒 blocked）调用 `record_gate_drop`  
2. **原因键**：`blocked_domain` / `garbage_snippet` / `wiki_track`（检索）；入库无 wiki  
3. **探针本轮**：`run_query_test` 对 hybrid 路径 snapshot before/after 求差  
4. **Hub**：探针 meta 行显示本轮；`/kb/stats` → KnowledgeBaseCard 一行累计  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-17-kb-gate-drop-counters.md` | 本方案 |
| `backend/app/services/kb_retrieval_gate.py` | counters + record/snapshot |
| `backend/app/services/kb_index.py` | 早拒记数；`kb_stats` 附带 |
| `backend/app/services/kb_query_test.py` | 本轮 delta |
| `backend/app/api/kb.py` | QueryTestResponse / KBStats 字段 |
| `frontend/src/api.ts` | 类型 |
| `frontend/.../RetrievalProbePanel.tsx` | 本轮丢弃展示 |
| `frontend/.../DependencyManager.tsx` | KB 卡累计 |
| `backend/tests/test_kb_gate_drop_counters.py` | 计数 / 探针 / stats |

## 3. 验证

- hybrid 丢弃 blocked → 本轮 `blocked_domain ≥ 1`；lifetime 递增  
- ingest blocked → stats `ingest.blocked_domain` 递增  
- 关 `content_filter_enabled` → 不再递增  
- 探针 UI 可见本轮丢弃文案  
