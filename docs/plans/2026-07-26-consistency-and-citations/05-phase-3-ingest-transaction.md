# 05 Phase 3 任务：ingest 事务一致性（TOCTOU + 单事务化）

> 前置：Phase 0/1/2 完成（head=0009，36 commits 已推送）。所有任务 TDD。
> 运行目录 `cd /root/FormuMind/backend`。
> 来源：Task 2.4 质量评审标注的已知限制 —— `POST /api/kb/ingest` 的 TOCTOU 竞态与三段式 commit（commit `f544482` 仅注释标注，未结构性修复）。

## 问题定义

### P1 TOCTOU（Time-of-Check-Time-of-Use）

`kb_index.ingest_full_document()` 的幂等检查与写入分属两个事务：

```python
existing = get_chunk_store().get_by_source(source_id)   # 事务 A：SELECT
if existing: return 0
return index_source(source_id, text)                     # 事务 B：DELETE+INSERT
```

并发窗口内两个请求都通过检查 → 重复执行 `replace_for_source`（DELETE+INSERT）。
虽因 `commit_session` 的调用级原子性不产生重复行，但 chunk UUID 全量重生成、
last-writer-wins 静默覆盖（同 source_id 不同 text 时数据漂移），且无法向客户端
返回正确的幂等语义（第二个请求应得 `chunk_count=0` 却得到重新计数）。

端点侧同样存在：`session.get(SourceDocument, source_id)` 检查后 `session.add()`
——并发重复提交时负方在 `commit()` 抛出未捕获的 `IntegrityError` → 500。

### P2 三段式 commit（部分失败留孤儿状态）

```
事务1 (kb.py:258-274): INSERT SourceDocument            → commit
事务2 (chunk_store:33): DELETE+INSERT document_chunks    → commit_session 内部 commit
事务3 (kb.py:279-290):  INSERT task_outbox               → commit
```

- 事务1 成功 + 事务2 失败 → SourceDocument 有行、无 chunks（孤儿文档）
- 事务2 成功 + 事务3 失败 → chunks 已写、outbox 无记录（完成事件丢失）
- 端点直接写库违反架构规则（`01-architecture.md`：api 薄控制器禁止直接写库）

## 目标语义

| 场景 | 目标行为 |
|------|----------|
| 重复提交同 source_id | 第二次返回 `chunk_count=0`，DB 行数不变，outbox 仍唯一 |
| 并发同 source_id（同/异 text） | DB 唯一约束裁决：胜方写入，负方幂等返回 0，无 500 |
| 中途失败（chunk 写入异常） | 整体回滚：无 SourceDocument 孤儿、无 chunks、无 outbox |
| outbox 入队失败 | 整体回滚：chunks 与 SourceDocument 一并撤销 |

## 任务分解

### Task 3.0 修复回归：chain-head 测试 0007→0009

**背景：** Task 2.2 添加 migration 0009 时，`test_revision_chain_head` 的连带更新
丢失（子代理提交丢失模式），当前 `pytest tests/test_alembic_migrations.py::test_revision_chain_head_is_0007` 失败。

**Files:** Modify `backend/tests/test_alembic_migrations.py`（函数名 + 断言 + docstring 引用）

**步骤：**
1. `pytest tests/test_alembic_migrations.py::test_revision_chain_head_is_0007 -q` 确认 FAIL（现状 RED）
2. 函数重命名 `test_revision_chain_head_is_0007` → `test_revision_chain_head_is_0009`，断言 `heads[0] == "0009"`，docstring 同步
3. 复跑 PASS；跑全文件回归

**验收：** `pytest tests/test_alembic_migrations.py -q` 全绿

### Task 3.1 document_chunks 唯一约束 `(source_id, ord)` + migration 0010

**目的：** 把 chunk 幂等从"应用层先查后写"升级为"DB 层强制"——并发重复插入
由唯一约束裁决，负方捕获 `IntegrityError` 转幂等响应，从根上关闭 TOCTOU 窗口。

**Files:**
- Create `backend/app/db/alembic/versions/0010_chunk_source_ord_unique.py`
- Modify `backend/app/db/models.py`（DocumentChunk 加 `UniqueConstraint`）
- Modify `backend/tests/test_alembic_migrations.py`（head 0009→0010）
- Create `backend/tests/test_chunk_unique_constraint.py`

**步骤：**
1. 失败测试：同 `(source_id, ord)` 两次 `session.add` + commit → `IntegrityError`
2. models.py `DocumentChunk.__table_args__` 加 `UniqueConstraint("source_id", "ord", name="uq_document_chunks_source_ord")`
3. migration 0010（遵循 §20 inspect-backfill 风格）：
   - **先去重**：`DELETE FROM document_chunks WHERE id NOT IN (SELECT MIN(id) ... GROUP BY source_id, ord)`（防御已有重复，当前生产数据应无重复，去重为幂等空操作）
   - inspect 已有约束，缺则 `op.create_unique_constraint`
4. bump chain-head 测试 0009→0010
5. 回归：`tests/test_ingest.py`、`tests/test_chunk_store.py`（若存在）、`tests/test_alembic_migrations.py`

**验收：** 新测试 ≥3 条全绿（唯一约束生效 / migration 幂等重跑 / upgrade+downgrade 双向）；上述回归全绿

### Task 3.2 ChunkStore 会话注入：`replace_for_source_in(session, ...)`

**目的：** 让 chunk 写入可以挂在调用方事务里，消除 `commit_session` 的独立 commit。

**Files:** Modify `backend/app/db/chunk_store.py`；Create `backend/tests/test_chunk_store_tx.py`

**契约：**
```python
def replace_for_source_in(self, session: Session, source_id: str, chunks: list[dict]) -> int:
    """在调用方 session 内 (re)write chunks。不 commit、不新建事务。
    生成器代际号 generation 由调用方 commit 成功后递增的责任仍归 store——
    提供 bump_generation() 公共方法，由事务拥有者在 commit 后调用。"""
```

**步骤：**
1. 失败测试：在调用方 session 中调 `replace_for_source_in`，`session.rollback()` 后 DB 无行（证明写入挂在调用方事务）
2. 实现：DELETE+INSERT 逻辑提取为 session 参数版本；原 `replace_for_source` 改为薄包装（`commit_session` + 委派 + generation 递增），行为不变
3. `bump_generation()` 公共方法
4. 回归：现有 chunk/ingest/search 相关测试

**验收：** 新测试 ≥3 条全绿；`pytest tests/test_ingest.py tests/test_hybrid_search.py -q` 回归绿

### Task 3.3 ingest 服务单事务化：`ingest_document_tx()`

**目的：** 把端点里的三段写入收拢为一个 service 函数、一个事务；端点回归薄控制器。

**Files:**
- Create `backend/app/services/ingest_tx.py`（或并入 `kb_index.py`，评审定）
- Create `backend/tests/test_ingest_tx.py`

**契约：**
```python
@dataclass
class IngestTxResult:
    source_id: str
    chunk_count: int     # 0 = 幂等命中（已存在）
    already_existed: bool

def ingest_document_tx(
    session_factory, *, source_id: str, text: str, title: str = "", metadata: dict | None = None
) -> IngestTxResult:
    """单事务：SourceDocument savepoint-upsert + chunk 幂等检查/写入 + outbox enqueue。
    唯一约束 IntegrityError → 幂等返回 chunk_count=0；其他异常整体回滚后抛出。"""
```

**步骤：**
1. 失败测试（对应目标语义四行）：
   - 重复提交 → `chunk_count=0`、`already_existed=True`、DB chunks 数不变、outbox 唯一
   - 并发模拟（monkeypatch 使首次检查后另一"请求"抢先写入）→ 唯一约束触发 → 幂等返回 0、无异常
   - chunk 写入抛异常（monkeypatch `replace_for_source_in` 抛错）→ 整体回滚：无 SourceDocument、无 chunks、无 outbox
   - outbox enqueue 抛异常 → 同样整体回滚
2. 实现：
   - SourceDocument upsert 用 §19 savepoint 模式（`begin_nested()` 在 `add()` 前、不 commit savepoint、命中 IntegrityError 后 re-select）
   - chunk 幂等：先 SELECT，未命中走 `replace_for_source_in`；`IntegrityError(uq_document_chunks_source_ord)` → 事务内重查，确认已存在 → `already_existed=True, chunk_count=0`
   - outbox `enqueue(session, ...)` 同事务
   - 唯一一次 `session.commit()`；成功后 `bump_generation()`
3. `ingest_full_document()` 保留为兼容包装（`reindex_all` 等旧调用方不动），内部委派

**验收：** 新测试 ≥5 条全绿（覆盖目标语义全表）

### Task 3.4 端点改薄 + 集成回归

**Files:** Modify `backend/app/api/kb.py`（`/ingest` 端点）；Modify `backend/tests/test_ingest.py`

**步骤：**
1. 端点删除直接写库代码（`default_session_factory`/`SourceDocument`/`enqueue` 导入），改为：
   ```python
   result = ingest_document_tx(default_session_factory(), source_id=..., text=..., title=..., metadata=...)
   return IngestResponse(source_id=result.source_id, chunk_count=result.chunk_count, status="ok")
   ```
2. `test_ingest.py` 增补：故障注入后再次 ingest 同 source_id 可正常完成（无半提交状态阻塞）；空 text 仍 400
3. 确认 `stores` fixture 的 `default_session_factory` monkeypatch 已覆盖新路径（§22 防漏）
4. 回归：`pytest tests/test_ingest.py tests/test_ingest_tx.py tests/test_chunk_store_tx.py tests/test_alembic_migrations.py -q`

**验收：** 端点无直接 `session.commit()`（grep 验证）；全部新旧 ingest 测试绿

## Definition of Done

- [ ] `test_revision_chain_head_is_0010` 绿；migration 0010 upgrade/downgrade/重跑幂等
- [ ] 目标语义四场景各有测试且全绿
- [ ] `/api/kb/ingest` 无直接写库（grep `session.commit\|default_session_factory` 于 kb.py 返回空）
- [ ] `pytest tests/test_ingest*.py tests/test_chunk*.py tests/test_alembic_migrations.py tests/test_outbox*.py -q` 全绿
- [ ] 无新增未跟踪临时文件；每任务一 commit

## 风险与回滚

| 风险 | 缓解 |
|------|------|
| 唯一约束上线时存量数据有重复 | migration 内先去重（MIN(id) 保留）；去重本身幂等 |
| 旧调用方（reindex_all/ingestion.py）行为改变 | `replace_for_source` / `ingest_full_document` 保留签名与语义，仅内部委派 |
| SQLite 与 Postgres 约束行为差异 | 约束为标准 SQL，两种方言一致；IntegrityError 捕获按 SQLAlchemy 通用异常 |
| generation 计数漏递增导致检索缓存脏 | `bump_generation()` 仅在 commit 成功后调用；Task 3.2 测试覆盖 rollback 不递增 |

**回滚：** 每任务独立 commit，可逐任务 revert；migration 0010 提供 downgrade（drop constraint）。
