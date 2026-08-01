# 01 目标架构与数据模型

## 1. 分层与模块边界（保持模块化单体）

```
app/
  api/            # 薄控制器：解析请求 → 调 service → response_model 输出（禁止直接写库）
  domain/         # Pydantic 契约：新增 CitationAnchor、ExternalDocument（纯 schema，无 IO）
  db/
    models.py           # ORM（新增表见 §2）
    alembic/            # 迁移（env.py + versions/）
    outbox_store.py     # 任务 outbox + idempotency（新增）
    doe_plan_store.py   # DOEPlan 持久化（新增）
    run_store.py        # OptimizationRun / RecommendationRun（新增）
  services/
    chunking.py         # Chunk 增加 char_start/char_end
    kb_index.py         # hybrid 检索 + anchor 生成
    ingestion.py        # fulltext-first 默认化（保留降级）
    citations.py        # 答案 ↔ anchor 绑定与校验（新增）
    reconcile.py        # Datalab ↔ SQLite 对账（新增）
  worker/tasks.py       # 改为经 outbox 入队（薄改）
```

**依赖规则（本次强制执行）：**
- `api → services → db`，反向禁止；`domain` 不 import 任何外层。
- 新增 store 一律 `get_xxx_store()` 工厂 + 构造可注入（测试传 session_factory），**禁止新模块级可变单例**。
- 配置只经 `get_settings()` 读取，但新服务在构造函数接收 `Settings`，便于测试替换。

## 2. 数据模型（新增/变更表）

### 新增 `doe_plans`（替换 `_PLAN_CACHE` 内存字典，workflow.py:190-208）
| 列 | 类型 | 说明 |
|----|------|------|
| plan_id | String(64) PK | 现有 plan_id（uuid hex） |
| domain | String(64) idx | 三大 domain |
| project_id | String(36) idx | 项目隔离 |
| design | String(32) | lhs / baybe_active … |
| payload | JSON | 完整 DOEPlan.model_dump() |
| engine | String(32) | native/pydoe/baybe/legacy |
| created_at / updated_at | DateTime | 审计 |

读写策略：`_cache_plan()` = 写库 + 内存 L1；`get_cached_plan()` = 先 L1 后 DB。导出端点不变。

### 新增 `task_outbox`（任务幂等 + 可重放）
| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | outbox row id |
| idempotency_key | String(128) unique | `kind:sha256(payload)[:32]` 或客户端传入 |
| kind | String(32) idx | search/recommend/deep_research/optimize/loop/kb_ingest |
| payload | JSON | 任务入参快照 |
| status | String(16) idx | PENDING/DISPATCHED/DONE/FAILED |
| celery_task_id | String(64) | 关联 Celery id |
| created_at / dispatched_at | DateTime | 重放与对账 |

行为：API 层先插 outbox（同事务查重 idempotency_key，命中直接返回已有 task handle），worker dispatch 后置 DISPATCHED。重启后 PENDING 行可重投。

### 新增 `optimization_runs` / `recommendation_runs`
关键列：`run_id PK, project_id idx, requirement_hash idx, engine, seed, code_version(git sha), input_snapshot JSON, result JSON, pareto_front JSON, created_at`。保证任何推荐/寻优结果可复现。

### 变更 `document_chunks`（强引用锚点）
新增列：`char_start INTEGER NULL`、`char_end INTEGER NULL`、`quote_hash VARCHAR(64) NULL`（quote 文本 sha256，用于校验漂移）。由 Alembic migration 添加；旧数据 NULL，`reindex_all()` 回填。

### 新增 `ingest_jobs`（全文入库幂等）
`id PK, source_id idx, origin_url idx, content_hash idx, status, error, created_at`——把现有内存式 kb_ingest 队列持久化，重复 URL/哈希直接命中已完成 job。

## 3. 一致性模型

| 场景 | 现状风险 | 目标语义 |
|------|----------|----------|
| API 重复提交（双击/重试） | 重复任务、重复入库 | idempotency_key 唯一约束 → 返回同一 task handle |
| worker 崩溃 | 任务丢失 | outbox PENDING 行启动时重投 |
| Datalab 部分成功 | SQLite 索引与 Datalab item 漂移 | saga 回滚（已有）+ 每日对账脚本（新增） |
| 后端重启 | DOE plan 丢失、无法导出 | plan 落库，export/闭环直接读库 |
| 升级后引用漂移 | chunk 重切导致旧引用失效 | quote_hash 校验；失效引用标记 `stale` 不静默展示 |

## 4. 强引用模型（#2 核心）

```
CitationAnchor {
  source_id, chunk_id,        # 定位到 DocumentChunk 行
  page_no | None,             # PDF 页码（已有）
  heading_path,               # 章节路径（已有）
  char_start, char_end,       # 原文偏移（新增）
  quote,                      # ≤240 字原文摘录（答案展示用）
  quote_hash                  # 漂移校验
}
```

数据流：`检索/入库 → DocumentChunk(带 offset) → kb_index 返回 evidence+anchor → chat/recommend 生成答案时 LLM 输出 [^n] → citations.py 建立 answer_claim↔anchor 映射并校验 quote 属于 chunk → API 返回 citations[]（含 anchor）→ 前端点击高亮原文区间`。

校验规则：quote 必须是对应 chunk 文本的子串（normalize 空白后）；claim_checker 升级：无 anchor 的数值/配方 claim 标记 `unsupported`。
