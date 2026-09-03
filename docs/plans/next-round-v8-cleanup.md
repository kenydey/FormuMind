# FormuMind 下一轮升级方案（基于真实 v7-freeze 代码扫描）

> 扫描基线：`v7-freeze`（1fdb1d5），2026-08-29
> 方法：对后端 223 文件 / 46K 行、前端 93 文件、173 测试文件做静态扫描 + 路由/调用交叉比对 + 索引/异常审计。
> 原则：每一项都有代码证据（文件+行号），无凭空设想。

---

## 一、扫描结论速览

| 方向 | 结果 | 严重度 |
|---|---|---|
| 硬编码密钥 / IP | **0 处** | ✅ 干净 |
| bare except（裸吞） | **0 处** | ✅ 干净 |
| TODO / FIXME | **1 处**（api_auth.py:178 注释） | ✅ 干净 |
| 前端 console 残留 | **1 处**（ErrorBoundary.tsx:19 合理日志） | ✅ 干净 |
| 数据库索引 | **完善**（每字段 index=True + 6 组复合索引，v7 已补 KG 复合索引） | ✅ 干净 |
| **死路由（HTTP 前端无调用）** | **6 处** | ⚠️ 待收敛 |
| **API 层测试缺口** | **2 个模块 0 测试** | ⚠️ 待补 |
| 异常降级（except Exception） | **60 处**，抽查 5 处核心路径均合理 | ⚠️ 待逐一审计 |

**代码库整体成熟度高**，v2~v7 的清理工作做得很好。本轮重点是**收敛遗留死入口 + 补测试缺口 + 异常审计**，非新增功能。

---

## 二、发现一：6 处死路由（产品收敛方向）

交叉比对方法：提取后端 120 个路由（含 main.py 挂载的 `/api` prefix）→ 对比前端 89 个去重 API 调用（含模板字符串归一化）→ 再全局 grep 确认「前端 src 0 调用 + 后端内部无 HTTP 转发」。

| # | 路由 | 定义位置 | 前端调用 | 后端内部 | 判断 |
|---|---|---|---|---|---|
| 1 | `POST /api/agents/review` | `app/api/agents.py:17` | 0 | `InitializeAgent` 被 `feasibility.py:47` **直接复用**（不走 HTTP） | HTTP 路由冗余，**类活跃不可删** |
| 2 | `GET /api/ingredients` | `app/api/formulations.py:59` | 0 | 无 | 真死路由 |
| 3 | `POST /api/materials/promote` | `app/api/materials.py:181` | 0 | 无 | 真死路由（"harvest trade products" 功能，未见任何入口） |
| 4 | `GET /api/kg/stats` | `app/api/kg.py` | 0 | 无（仅测试用） | 诊断路由 |
| 5 | `GET /api/kg/calibration` | `app/api/kg.py` | 0 | 无 | 诊断路由 |
| 6 | `GET /api/kg/path` | `app/api/kg.py` | 0 | 无 | 诊断路由 |

**关键证据**（避免误删）：

- `agents/review`：前端 0 调用，但 `InitializeAgent().review()` 被 `services/feasibility.py:47` 调用；`feasibility.check_formulation` 又被 `substitution.py:219`、`inverse_design.py:97` 内部调用。**可行性检查的真实链路是 `feasibility` 服务 → 前端经 loop/formulation 返回 `chemical_feasibility` 字段**（`LoopModal.tsx:137` 展示），从不直接打 `POST /api/agents/review`。→ 结论：只删 HTTP 路由，**保留 `agents/` 目录与 `feasibility.py`**。
- `materials/promote`：全库（前后端）grep 仅命中定义处 `materials.py:181` + docstring `:5`，无任何调用方。

**建议处理**（需你确认，符合"叠加模式→整合"偏好）：

- #1 `agents/review`：删除 HTTP 路由（功能已由 feasibility 服务承载），保留 agents 类。
- #2 `ingredients`：删除（已被 `templates/{domain}` + `meta` 取代）。
- #3 `materials/promote`：删除（无入口；若为未来功能则改为显式 TODO 而非悬挂路由）。
- #4~6 KG 诊断路由：保留但加 `tags=["debug"]` 并从 OpenAPI 主文档隐藏（`include_in_schema=False`），或删除。倾向**隐藏**（排查 KG 问题时仍有价值）。

---

## 三、发现二：2 个 API 模块 0 测试

| 模块 | 现状 | 缺口 |
|---|---|---|
| `app/api/materials.py`（6 路由） | `material_store` 有测试，**API 路由层 0 测试** | 路由入参校验、错误码、promote/availability/substitutes 响应契约无覆盖 |
| `app/api/design.py`（1 路由） | `inverse_design` 服务有测试，**API 层 0 测试** | `/api/design/inverse` 的请求校验、异步任务提交、错误路径无覆盖 |

**建议**：各补 1 个 API 层测试文件（`test_materials_api.py`、`test_design_api.py`），覆盖：正常返回、参数缺失 422、下游异常降级。成本低、收益确定。

---

## 四、发现三：60 处 `except Exception` 降级待审计

抽查 5 处核心路径，均为**合理降级**（有 fallback 或 `logger.exception`）：

| 位置 | 降级行为 | 是否吞错 |
|---|---|---|
| `main.py:93` | `logger.exception` 记录 outbox 恢复失败 | ✅ 合理 |
| `main.py:276` | `db_ok=False`（健康检查） | ✅ 合理 |
| `research_graph.py:652` | 注释明确「DB 不可达保守返回全部」 | ✅ 合理 |
| `optimizer.py:314` | `return self._random_point()`（优化失败降级随机） | ✅ 合理 |
| `training.py:87` | `return _RidgeModel()`（sklearn 不可用降级 numpy） | ✅ 合理 |

剩余 **55 处未逐一审计**，其中潜在风险点（需重点核实是否静默失败无日志）：

- `services/llm.py:1341`（LLM 主路径）
- `api/experiments.py:741`
- `db/datalab_client.py:137`
- `worker/tasks.py:110/179/283`（Celery 任务内吞错会导致任务「假成功」）

**建议**：做一轮「吞错审计」——对每处 `except Exception` 确认三点：①是否有日志 ②是否有降级/fallback ③是否有 re-raise。三项全无的即「真吞错」，补 `logger.exception` 或 re-raise。

---

## 五、优先级建议

| 优先级 | 项 | 理由 | 风险 |
|---|---|---|---|
| **P0** | 死路由收敛（#2 ingredients、#3 promote 删除；#1 agents 删路由保类；KG 诊断路由隐藏） | 符合产品收敛偏好，删冗余入口 | 低（已逐一确认无调用方） |
| **P1** | 补 materials/design API 层测试 | 确定性质量收益 | 低 |
| **P2** | 55 处 except 吞错审计 | 消除静默失败隐患 | 中（需逐一读码） |

---

## 六、实施计划（待你确认后执行）

1. **P0**：删 `ingredients`、`materials/promote` 路由 + 对应 schema/测试引用；`agents.py` 删 `@router.post` 保留 `agents/` 目录；KG 三个诊断路由加 `include_in_schema=False`。→ 跑全量测试确认无回归。
2. **P1**：新增 2 个 API 测试文件。→ 全绿。
3. **P2**：逐项审计 55 处 except，真吞错处补日志/re-raise。→ 全绿。

每项完成即提交，全程真实执行 + git 真实落库（本轮不再有幻觉输出）。
