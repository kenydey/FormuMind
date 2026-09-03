# 删除「工艺优化 (Process Optimization)」功能 — 实施计划

- **日期**：2026-08-26
- **决策来源**：用户确认该功能非 FormuMind 核心（核心 = 配方推荐 + DOE + 实验台账 + 知识图谱 + 自适应闭环）
- **评估方式**：代码逐行事实核查（OpenCode 免费模型后端两次返回服务端内部错误 `err_5677fe8a`/`err_7299f968`，故障时按 OpenCode skill 要求做人工复核，结论均附代码证据）
- **状态**：待用户确认后实施

---

## 1. 功能本质与必要性结论

### 1.1 该功能做什么（基于 `app/services/process_optimizer.py` 实际代码）

| 维度 | 事实 |
|------|------|
| 作用域 | 工艺**参数空间**寻优（固化温度/时间、分散转速、膜厚、浴温、pH、浸泡时间等） |
| 预测模型 | **硬编码经验公式**，非数据驱动、非 AI：<br>• `_predict_anticorrosion` — Arrhenius 固化转化 + 膜厚修正<br>• `_predict_degreaser` — Q10 温度模型<br>• `_predict_surface_treatment` — 幂律磷化膜重 |
| 优化器 | 复用 `optimizer.py` 的通用 `Factor` / `build_optimizer`（贝叶斯/Optuna/BoTorch 链） |
| 调用 LLM | ❌ 否 |
| 查询知识库 / KB | ❌ 否 |
| 读取实验台账 / 实测数据 | ❌ 否 |
| 依赖配方推荐 / DOE 结果 | ❌ 否（完全独立启发式） |
| 产出被核心功能消费 | ❌ 否（结果是孤岛状态，仅存项目存档） |

### 1.2 必要性评级：**低**

理由：
1. 预测模型为硬编码经验公式，与平台「AI 辅助研发、随积累变聪明」的定位不符；
2. 与配方主链路**零数据贯通**（不读台账实测、不写回配方体系），属装饰性旁路；
3. 用户已明确判定非核心。

### 1.3 删除可行性：**可删除，不影响其他功能**

全仓反向依赖核查结果：所有对工艺优化模块的引用均为「工艺优化写入**自身状态** / 项目存档**可选字段**」，**无任何核心功能读取它做下游决策**。

**唯一复用点说明**：`process_optimizer.py` 复用 `optimizer.py`（通用贝叶斯优化器）。本次**只删 `process_optimizer.py`（工艺专用封装）**，`optimizer.py` 保留——它仍被「寻优收敛 / DOE」核心功能使用。两者解耦，删除工艺优化不影响优化器。

---

## 2. 架构影响图

```
删除前依赖关系（→ 表示依赖/调用）：
  ActionsPanel(process入口) ──► ProcessOptModal
                                     │ api.optimizeProcess
                                     ▼
  POST /api/process-optimize ──► process_optimize.py ──► process_optimizer.py
                                                        ├──► schemas.ProcessOptRequest / ProcessOptResult
                                                        └──► optimizer.py (保留, 共享)

  项目存档链路（仅携带, 不被消费）：
  project_workspace.ProjectWorkspace.process_opt_result ──► ProcessOptResultPayload
  project_workspace.ProjectSummary.has_process_opt
  store: processOptResult / setProcessOptResult
  projectWorkspace.ts / api.ts(ProcessOptRequest,ProcessOptResult) / helpers.ts

删除后：上述整条链路移除，core 模块（配方/DOE/台账/KB/loop/optimizer）完全不受影响。
```

---

## 3. 文件变更清单（最小改动集合）

### 3.1 后端（6 处）

| # | 文件 | 改动 |
|---|------|------|
| B1 | `backend/app/main.py` | 删除 `from .api import process_optimize as process_router`（约 25 行）与 `app.include_router(process_router.router)`（约 192 行） |
| B2 | `backend/app/api/process_optimize.py` | **整文件删除** |
| B3 | `backend/app/services/process_optimizer.py` | **整文件删除** |
| B4 | `backend/app/domain/schemas.py` | 删除 `ProcessOptRequest`（约 664 行）与 `ProcessOptResult`（约 670 行）两个 schema 类 |
| B5 | `backend/app/domain/project_workspace.py` | 删除 `ProcessOptResultPayload` 类（23–29 行）、`ProjectWorkspace.process_opt_result` 字段（58 行）、`ProjectSummary.has_process_opt` 字段（81 行） |
| B6 | `backend/tests/` | 删除 `tests/test_process_optimizer.py`；从 `tests/test_integrations.py`（约 278 行）移除 `POST /api/process-optimize` 调用段 |

### 3.2 前端（10 处）

| # | 文件 | 改动 |
|---|------|------|
| F1 | `frontend/src/components/ActionsPanel.tsx` | 删除 `id:"process"` 卡片定义（约 44 行）、`const ProcessOptModal = lazy(...)` 导入（约 20 行）、`<ProcessOptModal />` 渲染块（约 330–342 行） |
| F2 | `frontend/src/components/ProcessOptModal.tsx` | **整文件删除** |
| F3 | `frontend/src/components/HistoryPanel.tsx` | 删除 `{project.has_process_opt && (...)}` 展示段（约 60 行附近） |
| F4 | `frontend/src/store/types.ts` | 删除 `ProcessOptResult` 导入（13 行）、`processOptResult` 字段（110 行）、`setProcessOptResult` 签名（198 行） |
| F5 | `frontend/src/store/index.ts` | 删除 `processOptResult: null` 初始值（约 51 行）与 persist 携带（约 109 行） |
| F6 | `frontend/src/store/slices/projectSlice.ts` | 删除 `setProcessOptResult` action（237–242 行）及其在 pick 列表的引用（245 行）；删除 `loadProject` 中 `draft.processOptResult = null`（121 行） |
| F7 | `frontend/src/store/helpers.ts` | 删除 `processOptResult` 的 patch 同步（约 70 行）与序列化（约 107 行） |
| F8 | `frontend/src/api.ts` | 删除 `optimizeProcess` 方法（1248–1249 行）与 `ProcessOptRequest`/`ProcessOptResult` 接口（1956–1968 行） |
| F9 | `frontend/src/projectWorkspace.ts` | 删除 `ProcessOptResult` 导入（15 行）、`has_process_opt` 字段（67 行）、`process_opt_result` 映射（94/128/188/239 行） |
| F10 | `frontend/src/store/helpers.test.ts` | 移除 `processOptResult` 测试引用（约 44 行） |

---

## 4. 实施步骤

1. **后端删除**：B2、B3 整文件删除；B1、B4、B5 精准 patch；B6 删除测试文件 + patch `test_integrations.py`
2. **前端删除**：F2 整文件删除；F1、F3–F10 精准 patch
3. **语法/类型校验**：
   - 后端：`.venv/bin/python -c "import app.main"` 验证无 import 错误；`ast.parse` 改动文件
   - 前端：`npx tsc -p tsconfig.json --noEmit` 必须零错误
4. **测试验证**（停 dev 服务避免 database is locked，见 §6）：
   - 后端：`pytest tests/test_workbench_loop.py tests/test_workbench_api.py tests/test_doe_*.py tests/test_recommend_*.py tests/test_formulation_*.py tests/test_auto_loop.py tests/test_integrations.py -q`
   - 前端：`helpers.test.ts` 等单测（移除工艺优化引用后）保持通过
5. **端点验证**：`curl POST /api/process-optimize` 应返回 404（路由已移除）；核心端点（/api/doe、/api/formulations、/api/experiments/workbench/*）正常
6. **提交**：分 1 个 commit（或后端/前端各 1 个），message 注明删除范围

---

## 5. 风险矩阵

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| main.py 漏删 import → 启动 ImportError | 中 | 高（服务起不来） | B1 整段移除 import + include_router；启动验证 |
| store 任一文件漏改 → 前端 TS 编译失败 | 中 | 中（构建失败，不破坏运行） | F4–F10 全列；`tsc --noEmit` 零错误门禁 |
| project_workspace 旧存档 JSON 含 `process_opt_result` key | 高 | 低（pydantic 忽略未知字段，不报错） | 保持向后兼容；无需迁移脚本 |
| test_integrations.py 误删其他集成测试 | 低 | 中 | 仅移除 process-optimize 段，保留其余 |
| `optimizer.py` 被误删 → 核心寻优/DOE 失效 | 低 | 高 | 本次**不删** `optimizer.py`，仅删 `process_optimizer.py` |
| `ProjectSummary.has_process_opt` 后端未实际赋值（grep 显示 projects.py 无写入） | — | 低 | 一并删除 schema 字段即可，无调用方 |

---

## 6. 回滚方案

- 本计划为纯删除，无数据迁移、无 schema 破坏性变更（pydantic 忽略未知字段）。
- 回滚方式：`git revert <commit>` 即可完整恢复。
- 验证：revert 后 `tsc --noEmit` + 后端 import + 核心测试重新全绿。

---

## 7. 验收标准（Definition of Done）

- [ ] `POST /api/process-optimize` 返回 404
- [ ] 前端 `tsc --noEmit` 零错误
- [ ] 后端 `import app.main` 成功
- [ ] 核心测试套件（workbench/doe/recommend/formulation/auto_loop/integrations）全绿
- [ ] 核心端点（doe / formulations / experiments/workbench）手动验证正常
- [ ] 提交并推送（或留本地待用户决定）

---

## 8. 待确认项（用户决策）

1. 删除后是否**保留** `ProjectSummary.has_process_opt` 字段做向后兼容（建议删，因无写入方）？
2. 提交后是否**立即推送**远端，还是仅本地提交待审阅？
3. 是否需要一并生成中文变更说明（面向 BASF 内部）？

> 默认按"全删 + 本地提交 + 暂不推送"执行，除非你另有指示。
