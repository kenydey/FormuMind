# FormuMind 工业级升级实施计划：#1 数据一致性底座 + #2 强引用知识库

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 把 FormuMind 从“聪明的配方助手”升级为“可审计、可复现、可交付的工业研发系统”——所有关键状态落库、任务幂等可重放、问答引用可追溯到原文页/偏移。

**Architecture:** 保持模块化单体（不拆微服务）。在现有 `app/db` / `app/services` 内新增小模块，用 Alembic 管理 schema，用 DB outbox + idempotency 保证任务一致性，用 `CitationAnchor` 把每条答案 claim 绑定到 `DocumentChunk` 的 page/offset。检索走 hybrid（关键词 + 余弦 + 现有 LLM rerank），不引入新向量库（YAGNI，pgvector 随未来 Postgres 落地）。

**Tech Stack:** FastAPI · SQLAlchemy 2 · Alembic · Celery+Redis · Pydantic v2 · pytest · 现有 ColBERT/TF-IDF/embedding 检索栈。

**计划文档结构：**
- `README.md`（本文件）— 目标、决策记录、里程碑
- `01-architecture.md` — 目标架构与数据模型
- `02-phase-0-1-consistency.md` — Phase 0/1 任务（数据一致性底座）
- `03-phase-2-strong-citations.md` — Phase 2 任务（强引用知识库）
- `04-standards-testing-rollout.md` — 代码规范、功能测试矩阵、风险与回滚
- `05-phase-3-ingest-transaction.md` — Phase 3 任务（ingest 事务一致性：TOCTOU + 单事务化）

---

## 决策记录（按“最优解”已定，无需再选）

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | Schema 迁移 | **Alembic**，替换 `db/database.py` 运行时 `ALTER TABLE` | 工业标准、可回滚、CI 可测 upgrade/downgrade |
| D2 | 主数据库 | **SQLite 保持 dev/CI 默认**；生产 Postgres（仅 docker-compose 加 profile，不在本期改代码路径） | 不破坏现有 759 测试；代码层不动 |
| D3 | 任务可靠性 | **DB outbox 表 + idempotency_key 唯一约束**，不引入 Redis Streams/Kafka | YAGNI；与现有 Celery+Redis 进度总线兼容 |
| D4 | DOE plan 缓存 | 新表 `doe_plans`；`workflow._PLAN_CACHE` 改为 DB-backed 薄读写层（保留函数签名） | 改动面最小；导出/闭环重启后不丢 |
| D5 | Datalab 一致性 | 保留双写+saga，新增 **reconciliation 对账脚本**（孤儿/缺失检测+修复报告） | 不重写 store，风险最低 |
| D6 | 强引用模型 | `CitationAnchor{source_id, chunk_id, page_no, char_start, char_end, quote}`；答案用 `[^n]` 绑定 | 页+偏移双锚点，可审计可高亮 |
| D7 | chunk 偏移 | `Chunk` 增加 `char_start/char_end`（dataclass 现仅 text/heading_path/page_no，已确认） | 引用可定位到原文区间 |
| D8 | 全文入库 | 生产默认开（`fulltext_enrich`），测试/dev 默认关；env flag 可回退 | 强引用依赖全文；成本可控（max_docs 上限） |
| D9 | 检索升级 | 本期 **hybrid score（关键词 + cosine）+ 现有 llm_rerank**，不上新向量库 | 改动集中在 `kb_index.py`；pgvector 留给 Postgres 阶段 |
| D10 | 评估门槛 | golden eval 集（JSONL）+ `scripts/eval_retrieval.py`，指标 Recall@k / citation precision / faithfulness，纳入 CI | 质量可度量、可回归 |

**明确不做（YAGNI）：** 微服务拆分、向量数据库、Kafka/Redis Streams、SSO/RBAC、OpenTelemetry（属后续 #4/#5 优先级）。

---

## 里程碑

| 里程碑 | 内容 | 验收 | 预估 |
|--------|------|------|------|
| M0 | Phase 0：Alembic 基线 + 测试加速 fixture | `alembic upgrade head` 于全新/现有库通过；`test_health` < 5s | 0.5–1 天 |
| M1 | Phase 1：一致性底座（outbox/idempotency/DOE plan 落库/run 记录/对账） | 重启后 DOE plan 可导出；重复提交不产生重复记录；对账报告为空 | 3–4 天 |
| M2 | Phase 2：强引用知识库（offset/全文/hybrid/引用绑定/评估） | 答案每条 claim 可展开原文页+区间；eval 指标达标 | 4–5 天 |
| M3 | 收尾：文档、前端引用展示、迁移指南 | README/迁移文档合并；前端引用点击高亮 | 1 天 |

总预估：**9–11 个工作日**（单人，含 TDD 与评审）。

## 实施工作流（每个任务统一节奏）

1. 按任务卡执行严格 TDD：RED（写失败测试并看到失败）→ GREEN（最小实现通过）→ REFACTOR。
2. 每任务一次 commit：`feat|fix|refactor|docs: <scope>`。
3. 每任务完成后跑两层验证：任务卡指定测试 + 受影响测试文件回归。
4. Phase 收尾跑全量 `pytest -q` 与前端 `npm run build`，并执行 `requesting-code-review` 流水线。
5. 提交计划内禁止项：新全局单例、运行时 DDL、无测试代码、网络依赖测试。
