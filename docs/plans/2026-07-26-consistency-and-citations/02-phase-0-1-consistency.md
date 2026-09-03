# 02 Phase 0/1 任务：数据一致性底座

> 每个任务 = RED→GREEN→COMMIT。测试统一放 `backend/tests/`。运行目录 `cd /root/FormuMind/backend`。

## Phase 0：工程准备

### Task 0.1 Alembic 基线
**Files:** Create `backend/app/db/alembic.ini`、`backend/app/db/alembic/env.py`、`backend/app/db/alembic/versions/0001_baseline.py`；Modify `backend/pyproject.toml`（deps + `alembic>=1.13`）、`backend/requirements.txt`（钉版本）
**步骤：**
1. 写失败测试 `tests/test_alembic_baseline.py::test_upgrade_head_on_empty_db`（tmp sqlite → `alembic upgrade head` → 表集合含 experiments/campaigns/source_documents/document_chunks/kb_*）
2. `pytest tests/test_alembic_baseline.py -v` 预期 FAIL（alembic 不存在）
3. 安装+初始化：`alembic revision --autogenerate -m baseline`（env.py 从 `default_session_factory` 取 URL，支持 `FORMUMIND_DB_URL` 覆盖）
4. 复跑预期 PASS；再 `alembic downgrade base && upgrade head` 双向通过
**验收：** 全新库 upgrade head 成功且表结构与 `Base.metadata` 一致

### Task 0.2 运行时 DDL 迁移 + 守护
**Files:** Modify `backend/app/db/database.py:28-126`；Create versions `0002..0006`（experiment/campaign/source_document/chunk/kb_entity_link 各列）
**步骤：**
1. 测试 `test_no_runtime_ddl_on_fresh_upgrade`：upgrade head 后 `make_engine()` 不执行任何 ALTER（用 SQLAlchemy event listener 断言）
2. 把 `_ensure_*` 各列固化为 migration；`database.py` 中 `_ensure_*` 改为**只读校验**（缺列 → `RuntimeError("run alembic upgrade head")`），保留 `_drop_legacy_workbench_table` 为 migration
3. 现有库迁移路径：`alembic stamp 0001 && alembic upgrade head`（写入 README 迁移指南）
**验收：** `pytest tests/test_db.py tests/test_campaign_schema_migration.py tests/test_kg_schema_migration.py -q` 全绿（这些测试改走 alembic）

### Task 0.3 测试加速 fixture
**Files:** Modify `backend/tests/conftest.py`、`backend/app/main.py:lifespan`
**步骤：**
1. 测试 `test_health` 在 `FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP=1` 下 < 5s（conftest setdefault）
2. lifespan 三处 bootstrap（secrets/colbert/compounds）读取该 flag 跳过；默认行为不变
**验收：** `time pytest tests/test_api.py::test_health -q` < 5s（当前 30s）

## Phase 1：一致性底座

### Task 1.1 task_outbox 表 + store
**Files:** Create `backend/app/db/outbox_store.py`、`tests/test_outbox_store.py`；Modify `backend/app/db/models.py`（+TaskOutbox）、`alembic/versions/0007_task_outbox.py`
**关键实现（契约）：**
```python
class TaskOutbox(Base):
    __tablename__ = "task_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```
**测试：** 同 key 二次 `enqueue()` 返回同一 row（IntegrityError 捕获→查回）；`mark_dispatched/mark_terminal` 状态机；`pending()` 只取 PENDING
**验收：** `pytest tests/test_outbox_store.py -v` ≥6 条全绿

### Task 1.2 API 入队改走 outbox（search/recommend/deep_research）
**Files:** Modify `backend/app/api/search.py:95-104`、`backend/app/worker/tasks.py`（TaskManager.submit_*）
**步骤：**
1. 测试：同一 payload 连发两次 `POST /api/search/stream` → 两个响应 task_id 相同，Celery `.delay` 只调一次（monkeypatch 计数）
2. `accepted_response()` 前置：`outbox.enqueue(kind, payload, idem_key)`；idem_key = `kind + sha256(json(payload, sort_keys=True))[:32]`（兼容客户端 `Idempotency-Key` header 优先）
3. dispatch 成功 `mark_dispatched(celery_id)`
**验收：** 幂等测试 PASS；`tests/test_search_incremental.py::test_search_stream_endpoint_returns_task_handle` 仍绿

### Task 1.3 重启重投 PENDING
**Files:** Modify `backend/app/main.py:lifespan`、Create `backend/app/services/outbox_replayer.py`
**测试：** 构造 PENDING 行 → 起 app（TestClient lifespan）→ `.delay` 被调用并置 DISPATCHED
**验收：** `pytest tests/test_outbox_replayer.py -v` 全绿

### Task 1.4 doe_plans 落库
**Files:** Create `backend/app/db/doe_plan_store.py`、`alembic/versions/0008_doe_plans.py`；Modify `backend/app/pipeline/workflow.py:190-208`
**步骤：**
1. 测试：`get_cached_plan` 在清内存后仍可读库返回；`_cache_plan` 幂等 upsert
2. `_PLAN_CACHE` 变 L1：读先内存后 DB 并回填；`_PLAN_CACHE_MAX` 只清内存
**验收：** 新增 6 测试 + `tests/test_doe.py::test_export_*`、`test_workbench_*` 回归全绿

### Task 1.5 optimization_runs / recommendation_runs
**Files:** Create `backend/app/db/run_store.py`、migration 0009；Modify `app/services/auto_loop.py`、`recommend_pipeline.py`
**测试：** 一次 loop/recommend 后库中有一条 run，`requirement_hash` 稳定、含 git sha 与 seed；同输入重跑产生新 run（审计追加，不覆盖）
**验收：** `pytest tests/test_run_store.py -v` 全绿

### Task 1.6 Datalab 对账脚本
**Files:** Create `backend/app/services/reconcile.py`、`backend/scripts/reconcile_datalab.py`
**测试（stub datalab_client）：** SQLite 有 item_id 而 Datalab 缺失 → 报告 `missing_in_datalab`；反之 `orphan_index`；全部一致 → 空报告 exit 0
**验收：** `pytest tests/test_reconcile.py -v` 全绿；`python scripts/reconcile_datalab.py --report-only` 在当前库输出报告
