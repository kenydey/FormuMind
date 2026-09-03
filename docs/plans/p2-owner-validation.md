# P2 Owner 校验 — Phase 1 预埋方案（零行为变更）

> 状态：单 token 模式（`FORMUMIND_API_TOKEN` 单值）无身份来源，强制校验 = 破坏可用性。
> 本方案 Phase 1 仅预埋数据层与校验锚点，行为上恒过，仅为多用户铺路。

## 1. 架构

```
Request
  → ApiAuthMiddleware (单 token 校验已存在)
  → get_current_owner(request)  // 新增：单 token 返回 "default"，多 token 预留解析
  → api/experiments.py / tasks.py
      assert_owner(resource.owner_id, current_owner)  // 单 token 恒过，记录 debug
  → store (Campaign / ExperimentRow / TaskOutbox).owner_id  // 新增 nullable 列
```

Phase 2 开关 `FORMUMIND_MULTI_USER=true` 时 `assert_owner` 切强校验 + 列表自动过滤（本次不实现，仅预留）。

## 2. 文件变更清单

| # | 文件 | 变更 |
|---|------|------|
| 1 | `app/db/models.py` | Campaign / ExperimentRow / TaskOutbox 加 `owner_id: Mapped[str|None] = mapped_column(String(64), nullable=True, index=True, default=None)` |
| 2 | `app/middleware/api_auth.py` | 新增 `get_current_owner(request) -> str` + `assert_owner(resource_owner, current_owner)` helper；`FORMUMIND_MULTI_USER` 未启用时恒返回 `default` |
| 3 | `app/api/experiments.py` | 3 处 TODO → 调用 `assert_owner`（288 create / 306 get / 320 sync） |
| 4 | `app/api/tasks.py` | 1 处 TODO → `assert_owner` |
| 5 | `alembic/versions/*` | 新增列迁移（nullable，无历史数据影响） |
| 6 | `tests/test_owner_phase1.py` | 新增 3 用例：单 token 恒过、owner 写入 default、未来强校验路径单测 |

## 3. 实施步骤（顺序执行）

1. models.py 加列（nullable，不影响现有 DB）
2. api_auth.py 加 helper（无副作用）
3. experiments.py / tasks.py 替换 TODO 为软校验（`logger.debug`）
4. 生成 alembic 迁移 `alembic revision --autogenerate`
5. 跑 `pytest tests/test_owner_phase1.py + workbench + tasks` 全绿

## 4. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 历史数据 owner_id 为 NULL | 100% | 无（nullable，查询不强制过滤） | Phase 1 不过滤 |
| 单 token 误判 403 | 0% | 无 | 单 token 分支恒过 |
| 迁移锁表 | 低 | 低 | 仅加 nullable 列，瞬间完成 |

## 5. 回滚

- `alembic downgrade -1` 删列
- 移除 `get_current_owner` 调用，恢复 TODO 注释即可（单文件回滚）

---
*基于 backend/app 实际扫描（models.py:172 Campaign / 38 ExperimentRow / 453 TaskOutbox，4 处 TODO），未编造。*
