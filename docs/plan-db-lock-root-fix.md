# 根治 database is locked：关闭裸 commit 侧门 + savepoint 加固

## 一、背景

P0（`_persist_fulltext` 入库重试）+ P1（`commit_session` 加 Redis 跨进程写锁）上线后，重跑 48 篇撞锁文档**仍发生 `database is locked`**（2 篇失败：Optimization、Ionic Transport），并伴生 `kg link_source` 的 `Can't reconnect until invalid savepoint transaction is rolled back` 错误（3 次）。

## 二、根因分析（两个，互为因果）

### 根因 1：裸 commit 侧门绕过 Redis 锁

P1 只给 `commit_session`（session_utils.py）加了 Redis 锁，但代码里仍有 **9 处直接 `session.commit()`** 的写路径**绕过锁**。SQLite 的写锁是**数据库级**的（不分表），任何绕过锁的裸写都会和走锁的入库并发撞锁。

### 根因 2：savepoint 异常处理缺陷

`begin_nested()`（savepoint）的 `except` **只捕获 `IntegrityError`**。当 `database is locked`（`OperationalError`）在 savepoint 内发生，会绕过 except，savepoint 未 rollback，session 进入「invalid savepoint」状态，后续 `session.commit()` 报 `Can't reconnect until invalid savepoint`。

**因果链**：

```
裸 commit 侧门 ──> database is locked ──> 在 savepoint 内发生
                                        └─> except 只捕 IntegrityError
                                        └─> savepoint 未 rollback
                                        └─> 污染 session
                                        └─> kg link_source 报 invalid savepoint
```

## 三、架构图（写路径现状 → 目标）

```
现状（写路径）：
  commit_session ──> Redis 锁 ──> SQLite  ✅ 串行
  裸 session.commit() ──> 直接 SQLite  ❌ 绕过锁，与上面并发撞锁
  begin_nested() savepoint ──> except IntegrityError only ❌ 撞锁后污染 session

目标（修复后）：
  commit_session ──> Redis 锁 ──> SQLite  ✅
  所有裸 commit ──> 改为走 commit_session  ✅ 统一串行
  begin_nested() savepoint ──> except 捕获锁冲突并 rollback  ✅ 不污染 session
```

## 四、文件变更清单

### P2：关闭裸 commit 侧门（9 处）

| # | 文件:行 | 功能 | session 来源 | 修复方式 |
|---|---|---|---|---|
| 1 | `api/kb.py:302,318` | 手动 API ingest（SourceDocument + outbox） | `factory()` | 改 `commit_session(factory)`，删显式 commit |
| 2 | `api/_idempotency.py:55` | outbox 幂等入队 | `factory()` | 同上 |
| 3 | `api/doe.py:41,85` | DOE 计划 + run record 持久化 | `factory()` | 同上 |
| 4 | `services/kg/entity_linker.py:67,386` | KG link_source + rebuild_all | `store._session_factory()` | 改 `commit_session`，删显式 commit |
| 5 | `db/store.py:333` | 实验记录（DatalabExperimentStore.add） | 外部传入 | 调用者改走 `commit_session` |
| 6 | `services/ingest_tx.py:171` | ingest 事务统一提交 | 外部传入 | 调用者改走 `commit_session` |
| 7 | `services/qc_ingest.py:123` | QC 报告入库 | 外部传入 | 调用者改走 `commit_session` |
| 8 | `pipeline/multimodal_fusion.py:67` | 多模态融合实体/关系写入 | 外部传入 | 调用者改走 `commit_session` |
| 9 | `db/dispatcher.py:101,107,130,137` | recover_stalled | 外部传入 | main.py 已加锁，改为显式走锁防回归 |

### P4：Redis 锁 TTL 调整（sqlite_lock.py）

| 文件 | 修改 |
|---|---|
| `db/sqlite_lock.py` | `timeout` 60 → **300s**（TTL，覆盖最长写事务）；`blocking_timeout` 从「= timeout」分离为**独立参数**（默认 300s），避免等待方过早降级导致并发 |

修改后签名：`sqlite_write_lock(redis_url, *, timeout=300.0, blocking_timeout=300.0)`。调用方（`commit_session`、`main.py`）无需改动，走默认值。

### P3：savepoint 异常处理加固（4 处）

| # | 文件:行 | 功能 | 修复方式 |
|---|---|---|---|
| 1 | `db/entity_store.py:120` | KG 实体 upsert | except 增加 `OperationalError` 分支：`sp.rollback()` 后 `raise` |
| 2 | `db/doe_plan_store.py:80` | DOE 计划保存 | 同上 |
| 3 | `db/outbox_store.py:59` | outbox 入队 | 同上 |
| 4 | `db/measurement_store.py:139` | 测量附件 attach | 同上 |

**savepoint 加固统一模式**：

```python
sp = session.begin_nested()
try:
    session.add(row)
    session.flush()
except IntegrityError:
    sp.rollback()
    ...  # 原并发去重逻辑
except OperationalError as exc:      # 新增
    sp.rollback()                     # 回滚 savepoint，不污染 session
    raise                             # 重新抛，交给上层 P0 重试
```

## 五、实施步骤时间表

| 步骤 | 内容 | 预估 |
|---|---|---|
| 1 | P3 先做（savepoint 加固，4 处，独立无依赖） | 15 min |
| 2 | P2 批次 A：`factory()` 来源的 4 处（kb/_idempotency/doe/entity_linker）改 `commit_session` | 30 min |
| 3 | P2 批次 B：外部传入 session 的 4 处（store/ingest_tx/qc_ingest/multimodal_fusion）改调用者 | 40 min |
| 4 | dispatcher 显式走锁防回归 | 10 min |
| 5 | 单测补充：savepoint 锁冲突回滚 + commit_session 覆盖 | 30 min |
| 6 | 全量测试 + 重跑 48 篇验证 | 1 h |

## 六、风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 改 `commit_session` 改变事务边界，引入隐性行为差异 | 中 | 中 | 逐文件改 + 跑对应单测；commit_session 语义与裸 with 一致（成功 commit/失败 rollback） |
| 外部传入 session 的调用者改造遗漏 | 中 | 高 | 全局 grep `session.commit()` 兜底清单，改造后复查 0 遗漏 |
| savepoint `raise` 后上层未捕获导致任务失败 | 低 | 中 | 上层 `_persist_fulltext` 已有 P0 重试兜底；qc/ingest 已有 try/except |
| Redis 锁 TTL 仍不够（极端超长写事务 > 300s） | 低 | 中 | 调至 300s 后基本覆盖；极端场景后续评估 watchdog 续期 |
| 改引入语法/导入错误 | 低 | 低 | 每文件 `py_compile` + 全量 pytest |

## 七、测试计划

1. `py_compile` 全部改动文件。
2. 单测：`test_session_utils`、`test_fulltext_fetcher`、`test_db`、`test_kb`、`test_api`（停 dev 服务跑）。
3. 新增单测：savepoint 在锁冲突（OperationalError）时正确 rollback、不污染 session。
4. 集成验证：重新触发 48 篇重跑，确认 `database is locked` 与 `invalid savepoint` 双消失，indexed 显著提升。

## 八、决策点（请评审确认）

1. **savepoint 加固采用 `raise`（重新抛）而非吞掉**——让上层 P0 重试兜底，避免静默丢数据。是否认可？
2. **外部传入 session 的 4 处**（store/ingest_tx/qc_ingest/multimodal_fusion）**改调用者**，而非在函数内部包锁——保证锁覆盖整个写事务（含 flush）。是否认可？
3. **Redis 锁 TTL 一并修改**：`timeout` 60s → 300s，`blocking_timeout` 分离为独立参数（默认 300s）——认可吗？
