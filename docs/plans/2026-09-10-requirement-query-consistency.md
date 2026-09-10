# P0 修复：requirement ↔ query 一致性

> 状态：待评审 → 实施中
> 日期：2026-09-10
> 目标：消除「新建项目 requirement 残留上一个项目值，污染检索扩展/配方推荐/DOE」这一类数据一致性问题

---

## 0. 根因（代码级，已核实）

| 位置 | 事实 |
|---|---|
| `project_store.py:167` | `create()` 无 requirement 时 `req = default_requirement()` |
| `project_workspace.py:120` | `default_requirement()` **硬编码** `防腐蚀环氧底漆 / carbon_steel / 500h / 70gsm / 80℃` |
| `api/projects.py:24` | `create_project` 直接把 `req.title` 当 title 传，**从不带 requirement** |
| `frontend projectSlice.ts:146` | `api.createProject(title)` 也不传 requirement |
| `search.py:34` `_assert_requirement_consistency` | **只校验 domain**，不校验 substrate / salt_spray_hours |

**结果**：新建项目（哪怕是「镁合金钝化剂/720h」）的 requirement 一律是环氧底漆/碳钢/500h，且检索前的一致性校验只看 domain（都是 anticorrosion_coating 就放行），于是 substrate 与 salt_spray 的污染**静默流入**检索扩展与后续推荐。

**已有资产（无需重建）**：`services/intent.py::parse_intent(text)` 已能把自然语言 brief 解析成带 domain/substrate/salt_spray_hours 的 `Requirement`（LLM 优先，离线正则兜底），且已通过 `POST /api/intent/parse` 暴露——只是**项目创建链路没调用它**。

---

## 1. 方案

### 改动 A：创建项目时用标题/query 派生 requirement（`project_store.py`）

`create(title, requirement=None)` 逻辑改为：

1. 显式传了 `requirement` → 用传的（保持现有行为，不破坏已填需求面板的流程）。
2. 没传 requirement 但有 `title` → 调 `parse_intent(title)` 派生 requirement。
3. 既没 requirement 也没 title → 才回退 `default_requirement()`（空项目兜底）。

要点：
- `parse_intent` 是纯函数、离线可用、失败也返回 heuristic 结果，**不会抛异常**；仍用 try/except 兜底到 `default_requirement()` 防回归。
- 派生后仍需走 `_insert` 里既有的 `project_id` 回写逻辑（`project_id` 空 → 回写 UUID），保证归属正确。

### 改动 B：一致性校验扩大到 substrate + salt_spray（`search.py`）

`_assert_requirement_consistency` 在现有 domain 校验之外，增加两项：

1. **substrate**：请求 requirement 的 `substrate` 与项目存储 `substrate` 不一致 → 409，提示先在需求面板切换基材。
2. **salt_spray_hours**：请求值非 0 且与存储值不同 → 409（或降级为 warning？见决策点）。

设计取舍（详见决策点）：substrate 是**硬阻断**（基材错=配方方向错）；salt_spray_hours 若硬阻断可能误伤「同一基材、重新定目标」的合法操作，故默认**软提示**——不阻断、返回 warning，但把不一致事实暴露出来让用户知情。

---

## 2. 文件变更清单

| 文件 | 改动 |
|---|---|
| `backend/app/db/project_store.py` | `create()` 加 title→intent 派生分支 |
| `backend/app/api/search.py` | `_assert_requirement_consistency` 加 substrate 硬校验 + salt_spray 软提示 |
| `backend/app/db/project_store.py`（测试）| 新增用例：带 title 创建→substrate/salt_spray 正确派生 |

---

## 3. 决策点（需拍板）

1. **salt_spray_hours 硬阻断 or 软提示？**
   - 建议：软提示（在响应里带 warning，不阻断）。理由：基材错了是方向性错误必须拦；盐雾目标从 500→720 是同一方向的参数调整，硬拦会误伤合法重试。
2. **是否同时修前端**（`projectSlice.ts` 创建时先调 `/api/intent/parse` 再带 requirement 创建）？
   - 建议：**本轮不修前端**。后端派生已覆盖根因，前端改动收益边际小且要动测试；后端单一真相源更稳。留待后续前端体验优化。

---

## 4. 风险与回滚

| 风险 | 缓解 |
|---|---|
| `parse_intent` 离线正则误判 domain（如把「防腐涂料」判成 surface_treatment）| 现有 `_DOMAIN_KEYWORDS` 已按优先级排（autodeposition 优先）；且派生失败兜底回 default |
| 派生触发额外 LLM 调用（有 key 时）| `parse_intent` 已有降级链（LLM 失败→离线），且仅在「新建项目且无 requirement」时触发一次 |
| substrate 硬校验误伤「先建项目后调 substrate」的流程 | 校验只在 `/api/search` 入口做；项目创建/保存不被拦，用户可正常在需求面板改基材后再检索 |

回滚：改动 A 是 `create()` 内局部逻辑，改动 B 是 `_assert_requirement_consistency` 内局部逻辑，二者独立；出问题分别 revert 对应 commit。

---

## 5. 验证

1. 单测：`parse_intent("开发一款用于镁合金表面处理的钝化剂...720小时")` → substrate=magic_alloy、salt_spray_hours=720、domain=surface_treatment。
2. `create(title="镁合金钝化...")` → 返回 detail.workspace.requirement 的 substrate/salt_spray 正确（非 carbon_steel/500）。
3. `POST /api/search` 带污染的 requirement（carbon_steel）而项目存储是镁合金 → 409 + 明确提示。
4. 回归：`create()` 无参 → 仍返回碳钢默认值（不破坏首次登录自动建空项目）。