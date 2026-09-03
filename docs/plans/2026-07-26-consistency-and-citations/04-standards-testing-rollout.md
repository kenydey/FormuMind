# 04 代码规范、功能测试与风险回滚

## 1. 代码规范（本期强制）

**TDD 铁律**
- 无失败测试不写生产代码；每个任务先 RED 后 GREEN；测试只测行为不测实现。
- 禁止“先写实现再补测试”；评审时无法说明“为何曾失败”的测试视为无效。

**分层与依赖**
- `api → services → db` 单向；`domain` 纯 schema；api 层禁止 SQL/httpx 直连。
- 新 store 用 `get_xxx_store()` 工厂 + 构造函数注入 session_factory；禁止新模块级可变单例（沿用现有 get_* 模式可改不可增）。
- 外部调用（Datalab/LLM/检索源）必须经 `errors.degrade_return / log_handled_exception` 分类处理，禁止裸 `except: pass`。

**Schema 与迁移**
- 所有表结构变更只经 Alembic；`database.py` 不再新增 `_ensure_*`；禁止运行时 DDL。
- 新列必须可空或有默认值 + 回填脚本（沿用 reindex_all 模式）。
- JSON 列用于易变负载；查询过滤字段必须提升为真实列并加索引。

**幂等与一致性**
- 所有会产生副作用的 POST 必须支持幂等（outbox 唯一键或自然键去重）。
- 双写（SQLite+Datalab）必须失败可回滚、可对账；新写路径默认走 `commit_session()`。

**其他**
- 类型注解全覆盖（`from __future__ import annotations`）；公开函数写 docstring（行为+降级语义）。
- commit：`<type>: <subject>`（feat/fix/refactor/test/docs），每任务一 commit。
- 测试禁止真实网络/真实 LLM；外部源用 stub/monkeypatch（沿用现有测试风格）。
- 日志：新代码用 std logging + 统一 context 前缀，不新增 loguru 调用点。

## 2. 功能测试矩阵

| 层级 | 范围 | 命令 | 通过门槛 |
|------|------|------|----------|
| 单元 | outbox/store/offset/hybrid/citations/reconcile | `pytest tests/test_outbox_store.py tests/test_citations.py tests/test_reconcile.py -v` | 100% 绿，无网络 |
| 迁移 | alembic 双向 | `pytest tests/test_alembic_baseline.py -v` + 手工 `alembic downgrade base && upgrade head` | 全新库与现有库均通过 |
| 幂等 | 重复提交/outbox 重投/双写回滚 | `pytest tests/test_outbox_replayer.py tests/test_campaign_store_datalab.py -v` | 同 key 单任务；saga 回滚无残留 |
| 契约 | Datalab payload、LLM structured、检索源 | `pytest tests/test_datalab_payload_contract.py tests/test_llm_structured.py -v` | 全绿（stub） |
| 回归 | 受影响面 | `pytest tests/test_kb_index.py tests/test_kb_grounding.py tests/test_chat_structured.py tests/test_doe.py tests/test_workbench_api.py tests/test_auto_loop.py -q` | 全绿 |
| 全量 | 759+ | `pytest -q` | 全绿（M0 后应 < 10 min；当前超时项单独治理） |
| 检索评估 | golden 集 | `python scripts/eval_retrieval.py` | Recall@10≥0.8，citation precision≥0.9，unsupported≤0.2 |
| 前端 | 类型+构建 | `cd frontend && npm run build` | 通过；引用 chip 手测可展开 |
| 性能 smoke | KB 入库 100 篇 | `python scripts/bench_ingest.py`（随 Task 2.4 提供） | p95 单篇 < 5s（离线解析） |

**每个 Phase 收尾 Definition of Done：** 对应矩阵全绿 + `git log` 每任务一 commit + 计划文档勾选完成项 + 无新增未跟踪临时文件。

## 3. 风险与回滚

| 风险 | 影响 | 缓解 / 回滚 |
|------|------|------------|
| Alembic 基线与现有库漂移 | 老库 upgrade 失败 | `alembic stamp 0001` 后逐版本校验列存在性；保留旧 `_ensure_*` 只读守护一版，下版本删除 |
| chunk 偏移回填错误 | 引用指错区间 | quote_hash 校验标 stale；`reindex_all()` 可全量重建；回滚=降级到 page/heading 级引用 |
| fulltext 默认开带来成本/速率压力 | 外部源限流、入库变慢 | `fulltext_max_docs/timeout` 上限 + env flag 一键关；ingest_jobs 失败隔离 |
| hybrid 改分影响现有排序 | 检索质量回退 | α/β 可配，默认参数经 golden eval 验收后才合并；eval 失败不合并 |
| outbox 改入队路径引入回归 | 任务发不出 | 保留 direct dispatch 开关（env），灰度：先双写观察一天再切主路径 |
| 759 全量测试过慢 | CI 不可用 | M0 fixture 提速 + 后续 pytest-xdist；慢测试清单单独治理（不阻塞本期交付） |

## 4. 交付物清单

- Alembic 迁移链（0001–0010+）与《迁移指南》（README 增补）
- 一致性底座：outbox/idempotency、落库 doe_plans、run 记录、对账脚本
- 强引用：CitationAnchor 全链路（chunk offset → evidence → answer [^n] → 前端高亮）
- golden eval 集 + eval 脚本 + CI 门槛
- 测试：新增 ≥40 条，回归全绿；前端构建通过
