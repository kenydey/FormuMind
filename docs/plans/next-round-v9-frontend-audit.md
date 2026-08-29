# FormuMind 前端↔后端 API 交叉审计（v9 候选）

> 基线：main @ bfd0b44（v8 三项已闭环），2026-08-29
> 方法：提取前端 src 84 个去重 API 调用（模板字符串归一化 + 查询串剥离）→ 与后端 OpenAPI 107 路由交叉比对 → 逐条人工核验（宽松 grep + 关键行阅读 + 后端内部引用排查）。

---

## 一、结论速览

| 方向 | 结果 | 严重度 |
|---|---|---|
| 前端死调用（调了不存在的路由） | **0 处**（4 处疑似全部为提取误报） | ✅ 干净 |
| 后端路由前端 0 调用 | 27 条候选 → 核验后 **18 条真·无前端入口** | ⚠️ 待产品决策 |
| 服务层活跃但 HTTP 路由死 | 4 条（与 v8 `agents/review` 同类：服务保留、路由可收敛） | ⚠️ 低 |
| 官方已废弃路由 | 1 条（`research/expand` deprecated=True） | ✅ 明确可删 |

---

## 二、发现一：前端死调用 = 0（误报明细，避免误删）

交叉比对报出 4 处疑似死调用，逐一核验全部为提取工具误报：

| 疑似 | 真相 | 证据 |
|---|---|---|
| `/api/experiments/attachments` | `readApiError` 错误描述字符串，非真实 fetch | api.ts:946 |
| `/api/experiments/workbench/attachments` | 同上（真实路径在 940 行模板字符串） | api.ts:974 |
| `/api/experiments/import-csv{}` | 查询串拼接 `${q}`（`?domain=…`）被误当路径段 | api.ts:1078 |
| `/api/tasks/abc/stream` | 测试断言字符串 | api.test.ts:163 |

**前端无任何指向已删 v8 路由的残留调用**（v8 删除 3 路由安全）。

---

## 三、发现二：18 条后端路由前端 0 调用（分级）

核验过程中排除的 9 条（**非死路由**）：`doe/history`、`kb/sources`、`experiments/import-csv`（提取漏捕，真实调用）；`/health`、`/health/detailed`（运维）；`chemical/enrich-materials`、`kb/hybrid-search`、`kb/search`、`kg/retrieve`（**服务层活跃**：main.py lifespan / research_graph / kg retrieval 直接调服务函数，不走 HTTP）。

剩余 18 条真·前端无入口：

| # | 路由 | 定义位置 | 判定 | 建议 |
|---|---|---|---|---|
| 1 | `GET /api/research/expand` | research.py:128 | **官方 deprecated=True** | **删**（低风险） |
| 2 | `GET /api/search/expand` | search.py:105 | 注释自称 debug endpoint | **删或隐藏** |
| 3 | `POST /api/qc/analyze` | qc.py:42 | 保留的 CV 分析 stub（文档标 reserved） | **删或隐藏** |
| 4 | `GET /api/materials` | materials.py:101 | 无前端 UI（仅 substitutes 弹窗用 `/substitutes`） | 待产品确认 |
| 5 | `GET /api/templates/{domain}` | formulations.py:75 | **v8 计划误判**：声称是 ingredients 替代品，实际前端也不用 | 待产品确认 |
| 6 | `POST /api/train` | experiments.py:180 | 前端经 `POST /api/experiments`（retrain=true）触发训练 | 待产品确认 |
| 7 | `GET /api/experiments/search` | experiments.py:719 | 后端 Datalab 搜索功能；前端用列表+搜索框替代？ | 待产品确认 |
| 8 | `POST /api/experiments/hooks/convergence` | experiments.py:806 | 收敛钩子，前端无 UI | 待产品确认 |
| 9 | `GET /api/experiments/workbench/{}/rounds` | experiments.py:925 | 台账轮次契约 | 待产品确认 |
| 10 | `PUT /api/experiments/workbench/{}/rows/{}/tags` | experiments.py:658 | 台账行级标签（记忆：行级操作方向） | 待产品确认 |
| 11 | `GET /api/formulations/versions/detail/{}` | formulations.py | 版本详情，前端用 versions/{id} | 待产品确认 |
| 12 | `GET /api/kb/chunks/by-source/{}` | kb.py:180 | 切块查看（KB 诊断） | 隐藏或保留 |
| 13 | `POST /api/kb/ingest` | kb.py:245 | 文档标注任务 3.3 已改造为事务模式 | 待产品确认 |
| 14 | `GET /api/kb/integrity` | kb.py:342 | **文档 §5.20 记载特性**（引用完整性巡检），前端未接入 | 保留（产品确认） |
| 15 | `GET /api/kb/products` | kb.py:150 | 商业产品登记簿；前端仅展示 stats.products 计数 | 待产品确认 |
| 16 | `POST /api/kg/rebuild` | kg.py:128 | KG 重建管理 | 保留（v9 KG 深化方向） |
| 17 | `POST /api/kg/link-source/{}` | kg.py:140 | KG 单源关联 | 保留（v9 KG 深化方向） |
| 18 | `GET /api/train` 系扩展 | — | — |
| 19 | `GET /api/sources/{}` | ingest.py:143 | 单源详情/删除；前端经 projectWorkspace 本地管理 sources 数组 | 隐藏（B 组同款） |

> 注：`/api/sources/{}` 为执行时补录（初版报告疏漏），已按 B 组处理。

---

## 四、建议处理（待确认后执行）

- **A 组（删，低风险）**：#1 `research/expand`（官方 deprecated）、#2 `search/expand`（debug）、#3 `qc/analyze`（reserved stub）。删除 + 对应测试/文档引用清理。
- **B 组（删或隐藏，中风险，需产品确认）**：#4 `materials` GET、#5 `templates/{domain}`、#6 `train`、#7 `experiments/search`、#8 `hooks/convergence`、#11 `versions/detail`、#13 `kb/ingest`、#15 `kb/products`。倾向 **`include_in_schema=False` 隐藏**（保留后端契约，避免未来功能回归要重建）。
- **C 组（保留，理由充分）**：#9/#10 台账轮次/行级标签（行级操作整合方向）、#14 `kb/integrity`（§5.20 特性）、#16/#17 KG 重建/关联（v9 知识图谱深化）、#12 `kb/chunks/by-source`（KG/KB 诊断）。

## 五、风险矩阵

| 项 | 风险 | 缓解 |
|---|---|---|
| 删 A 组 3 条 | 低（deprecated/debug/stub，无调用方） | 全量测试 + OpenAPI 复核 |
| B 组隐藏 8 条 | 中（未来功能可能引用） | 隐藏不改实现，恢复成本 = 去掉一个参数 |
| C 组不动 | 无 | — |
| 误删服务活跃路由 | 已排除（4 条服务活跃已识别） | 每条均有后端引用证据 |

## 六、执行状态（2026-08-29）

- [x] A 组 3 条已删（research/expand、search/expand、qc/analyze）+ 4 个测试 + 2 处文档清理
- [x] B 组 8 条已隐藏 + 补录 `/api/sources/{}` 隐藏（共 9 条 include_in_schema=False）
- [x] 审计脚本固化 `scripts/verify_frontend_api.py`
- [x] 全量测试通过，分 2 commit（A 删 / B 隐藏+脚本）

## 七、联动说明

- v8 计划称 `ingredients` 被 `templates/{domain}` + `meta` 取代——**实际 templates 前端也不调用**，该结论需修正（#5）。
- 审计脚本可固化为 `scripts/verify_frontend_api.py` 供后续版本复用。
