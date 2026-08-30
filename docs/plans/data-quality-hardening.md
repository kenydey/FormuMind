# 实验数据质量与 Datalab 同步健壮性 — 实施计划

> 目标：修复本次排查暴露的数据链路缺陷，让"数据驱动配方研发"建立在可信、可观测的实验数据之上。
> 纯增量改造，不影响任何现有功能与数据。

## 1. 背景与问题诊断

### 1.1 本次排查发现的具体缺陷

| # | 缺陷 | 位置 | 影响 |
|---|---|---|---|
| D1 | 48 条 Datalab 失效引用导致 `list_rows` 每次查询反复 skip + warning | `campaign_store.py:495` | 日志噪音；本地 `sample_refs` 与 Datalab 永久不一致 |
| D2 | `molar_mass` 以字符串回灌，触发 Pydantic 序列化告警 + Celery "Logging error" | `schemas.py`(已修) / 回灌链路 | 类型漂移污染下游模型 |
| D3 | 非法测量值 / 未知 key 被**静默丢弃**，无告警、无记录 | `_numeric_measured` / `validate_measurements` | 数据质量黑洞：用户不知道哪些数据被丢弃 |
| D4 | Datalab 未就绪时 backend 启动崩溃循环 | `training.py`(已修 registry) | 已修复，但需复核其余模块级初始化 |

### 1.2 数据链路现状（已确认）

```
前端台账 Completed 行
   │  PUT /api/experiments/workbench/sync
   ▼
campaign_store.batch_sync ──► Datalab items（SSOT）
   │  validate_measurements（strict→fallback，静默丢未知 key）
   ▼
workbench_training.ingest_workbench_rows
   │  row_to_experiment_record（_numeric_measured 静默丢非法值）
   ▼
ModelRegistry（training，label 幂等去重）
```

**关键结论**：链路已有基本校验，但全部是「静默丢弃」模式——数据质量问题不可见、不可追踪、不可修复。

## 2. 方案设计

### 2.1 改造一：数据校验告警化（D2 / D3）

把「静默丢弃」升级为「丢弃 + 记录 + 汇总」，让数据质量问题显性化。

- `_numeric_measured(measurements)` → 返回 `(clean: dict, dropped: list[str])`，每条丢弃记录 `logger.warning`（含 key + 原始值 + 原因）
- `validate_measurements(..., strict=False)` → 记录被丢弃的未知 key（`logger.warning`，汇总去重）
- 回灌结果 `ingest_workbench_rows` 返回体新增 `quality: {dropped_values: int, unknown_keys: [...], ...}`，写入 `loop_history`（复用现有 `prediction_bias` 同款机制）

### 2.2 改造二：Datalab 对账与失效引用自动清理（D1）

新增对账能力，取代「每次查询重复 skip warning」：

- 新增 `reconcile_sample_refs(campaign_id) -> dict`：
  - 批量探测 `sample_refs` 各 `item_id` 在 Datalab 的存在性（复用 `/get-item-data` 404 语义）
  - 返回 `{removed: [...], kept: [...], removed_count}`，移除失效引用并持久化（复用 `_save_sample_refs`）
  - 幂等；有失效时 `logger.warning` 报告清理结果
- 集成点：
  - **查询时自动清理**：`list_rows` 遇到 404 时，累积失效 item_id，达到阈值（如 ≥1）后同步清理该 campaign 的失效引用
  - **手动对账 API**：`POST /api/experiments/workbench/{campaign_id}/reconcile`，返回清理明细
- 可选（阶段二）：Celery 定时全量对账（遍历所有 campaign），批量清理 + 汇总报告

### 2.3 改造三：数据质量可见性（阶段二，可选）

- `GET /api/experiments/workbench/{campaign_id}/quality` 返回该 campaign 的数据质量快照（失效引用数、历史丢弃统计）
- 前端台账页加数据质量徽标（可选，依赖前端改动量评估）

## 3. 架构图（数据流）

```
                    ┌─────────────────────────────────────────┐
  台账 Completed 行 ─►│  batch_sync                             │
                    │   ├─ validate_measurements ── 告警化 ✓   │
                    │   └─ 失效引用 → reconcile 自动清理 ✓      │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  workbench_training.ingest_workbench_rows │
                    │   ├─ _numeric_measured ── 告警化 ✓        │
                    │   └─ quality 汇总 → loop_history ✓        │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  ModelRegistry（label 幂等）              │
                    │   + Datalab 对账（reconcile API / 自动）  │
                    └─────────────────────────────────────────┘
```

## 4. 文件变更清单

| 文件 | 改动 | 类型 |
|---|---|---|
| `backend/app/domain/objective_contract.py` | `validate_measurements` non-strict 记录丢弃 key | 改 |
| `backend/app/services/workbench_training.py` | `_numeric_measured` 返回丢弃清单；`row_to_experiment_record` / `ingest_workbench_rows` 汇总 quality | 改 |
| `backend/app/db/campaign_store.py` | 新增 `reconcile_sample_refs`；`list_rows`/`batch_sync` 集成自动清理 | 改 |
| `backend/app/api/experiments.py` | 新增 `POST /experiments/workbench/{campaign_id}/reconcile` | 改 |
| `backend/tests/test_workbench_training.py` | 补：告警化丢弃、quality 汇总断言 | 改 |
| `backend/tests/test_campaign_store.py`（如存在） | 补：reconcile 幂等、失效引用清理断言 | 改 |

> 注：`CampaignStoreInterface`（抽象基类）与 `SqliteCampaignStore` 需同步 `reconcile_sample_refs` 签名（sqlite 实现为 no-op 或本地对账）。

## 5. 实施步骤与时间表

| 步骤 | 内容 | 验证 |
|---|---|---|
| 1 | 改造一：校验告警化（objective_contract + workbench_training） | 单测断言 dropped 清单 + warning 日志 |
| 2 | 改造二：reconcile_sample_refs + 接口层集成 | 单测：失效引用清理幂等、kept 不变 |
| 3 | 新增 reconcile API 端点 | 接口测试：返回 removed 明细 |
| 4 | `SqliteCampaignStore` / 接口签名同步 | 全量测试无回归 |
| 5 | 部署 + 真实数据对账（当前 48 条已手动清过，验证幂等空结果） | 真实 API 调用 + 前端台账加载 |

## 6. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 自动清理误删有效引用（Datalab 瞬时 404） | 低 | 高 | 清理前二次探测确认；非 404 错误（超时/5xx）**不**清理；提供 reconcile 结果明细可回溯 |
| 告警化导致日志量激增 | 低 | 低 | warning 按 campaign 聚合去重，非逐条刷屏 |
| `SqliteCampaignStore` 签名不同步 → 类型错误 | 中 | 中 | 步骤 4 单独处理 + 全量测试覆盖 sqlite 分支 |
| 回灌返回体新增字段破坏前端 | 低 | 低 | 新增字段为增量，前端不解析则忽略（向后兼容） |

## 7. 验收标准

1. 非法测量值/未知 key 被丢弃时，日志出现 warning 且回灌返回体 `quality` 字段有统计
2. `reconcile_sample_refs` 幂等：重复调用第二次返回 `removed: []`
3. 失效引用清理后，`list_rows` 不再对同一 campaign 重复 skip warning
4. 全量测试无回归（当前基线 1605 passed）
5. 真实环境：对账 API 对已清理的 campaign 返回空结果（幂等验证）

## 8. 待决策

1. 失效引用清理策略选 **A（查询时自动清理）** 还是 **A+B（自动 + 手动 API）**？—— 建议 A+B，成本低且可回溯
2. 是否纳入 **阶段二（数据质量报告 + 前端徽标）**？—— 建议先做阶段一（纯后端），跑稳后再评估前端
