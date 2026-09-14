# FormuMind Project Dossier Wiki（健壮模板 + 事件更新 + Report 地基）

状态：**P4 蓝图修订版（取代窄版 project_tech 草稿）**  
日期：2026-09-14  
取代：[`2026-09-14-wiki-project-theme-template.md`](./2026-09-14-wiki-project-theme-template.md)（保留为历史动机；**以本文为准**）  
父蓝图：[`2026-09-13-wiki-compiled-memory.md`](./2026-09-13-wiki-compiled-memory.md)  
ADR：[`../architecture/ADR-2026-09-13-wiki-compiled-memory.md`](../architecture/ADR-2026-09-13-wiki-compiled-memory.md)

---

## 0. 目标一句话

把 **Project Dossier Wiki**（`themes/project-{id}.md`）做成 FormuMind 的 **项目级编译卷宗**：

- 覆盖核心产品数据：技术要求、文献/RAG、配方、DOE、实验台账、寻优/闭环、图表与附件；
- **可增量更新**（检索后写、改要求后更、DOE/台账后更、闭环后更）；
- 正文给人读，**front-matter + 锚定数据块**给机器读 → 为后续 Hub **Report** 提供同一套 ContextPack，避免再抽一遍。

信任边界不变：Claims 只 Raw；DOE 硬边界不静默吃 L2；L1 实体页不被 LLM 覆盖。

---

## 1. 为什么要改写上一版模板

上一版 `project_tech` 五节偏「硅烷技术长文」，对 FormuMind 产品闭环覆盖不足：

| 缺口 | 影响 |
|------|------|
| 未锚定 `project_id` / `Requirement` / `constraint_values` | 改技术要求后无法精确补丁 |
| 未结构化挂接 `doe_plans` / `loop_history` / `measurements` | DOE/台账更新只能整页重写，易丢历史 |
| 无图表/附件槽位 | 寻优曲线、Pareto、SEM/结构图无法稳定进卷宗 |
| 未定义「节级 patch」 | LLM 一写全篇 → 抖动大、成本高、难做 Report 差分 |
| 与 Hub Reports 占位未打通 | 日后 Report 仍会另起炉灶 |

本修订把 Wiki 定位为：**Report 的上游卷宗（Dossier）**，而不是一次性散文。

---

## 2. 内容分层（保持 + 扩展）

```text
L0  解析/OCR → Raw chunks（证据 SSOT）
L1  实体 Wiki（确定性）：materials / chemicals / systems / mechanisms / pitfalls
L2a 体系综述 theme（已有 system_overview，旗标关）
L2b Project Dossier（本计划，旗标关）← 项目卷宗，Report 主输入
L3  Report（远期）：从 Dossier + 可选 LLM 排版生成 briefing / feasibility / …
```

| 层 | 谁写 | 谁读 | 进 Claims？ | 进 DOE 硬边界？ |
|----|------|------|-------------|-----------------|
| L0 Raw | 解析/入库 | Claims / Track B | ✅ | 间接（实验值） |
| L1 | 确定性编译 | Chat / DOE soft hint | ❌ | soft hint only |
| L2b Dossier | 骨架确定性 + 叙述 LLM | Hub/Chat Reader / **Report** | ❌ | ❌ |
| Report | 排版 LLM/模板 | 对外交付 | ❌（引用须回链 Raw） | ❌ |

---

## 3. 冻结模板：`project_dossier`（v1）

路径：`themes/project-{project_or_campaign_key}.md`  
`kind: theme` · `template: project_dossier`

### 3.1 Front-matter（机器可读，Report 必用）

```yaml
---
kind: theme
template: project_dossier
schema_version: 1
llm_generated: true          # 叙述段；表格/数字以 data 块为准
reviewed: false
project_id: "…"
campaign_id: "…"             # 可空
workbench_campaign_id: "…"   # 可空
domain: "anticorrosion_coating"
updated_at: "ISO-8601"
section_revisions:           # 节级版本，便于增量与 Report 缓存
  S1_requirements: 3
  S2_literature: 5
  S3_baseline_formula: 2
  S4_doe: 4
  S5_lab_ledger: 6
  S6_optimize_loop: 3
  S7_artifacts: 2
  S8_open_questions: 1
source_ids: ["…"]            # 文献/附件 Raw id 并集
l1_paths: ["chemicals/…", "systems/…"]
doe_plan_ids: ["…"]
experiment_ids: ["…"]        # 或 datalab item ids
artifact_ids: ["…"]
model: ""
prompt_hash: ""
---
```

### 3.2 正文骨架（稳定标题 = patch 锚点，禁止随意改名）

```markdown
# [[项目标题]]

> **领域**：… · **基材**：… · **状态**：进行中/收敛/暂停  
> **卷宗**：Project Dossier · 数据截止 `updated_at`

## S1. 技术要求与目标指标
<!-- data:requirements -->
| 指标 / 约束 | 目标值 | 单位 | 方向 | 来源 |
| :--- | ---: | :--- | :--- | :--- |
| salt_spray_hours | 500 | h | maximize | Requirement |
| voc_limit_gpl | 420 | g/L | upper_bound | Requirement |
| … | … | … | … | constraint_values / objectives |

叙述（可 LLM）：问题背景、应用场景、必须满足的工艺窗口。

## S2. 文献与证据地图
<!-- data:literature -->
| 主题簇 | 代表来源 | source_id | 要点（≤40字） | Wiki/L1 |
| :--- | :--- | :--- | :--- | :--- |

叙述：机理共识 / 争议；保留上游图片：
![](images/…)

## S3. 基准配方与物料
<!-- data:formula -->
| 组分 | 角色 | Wt% 或 g/L | CAS/牌号 | L1 链接 | 来源 |
| :--- | :--- | ---: | :--- | :--- | :--- |

活性配方 / leaderboard Top-1；单位必须显式（wt% vs g/L）。

## S4. DOE 设计与结果摘要
<!-- data:doe -->
| 轮次 | design_type | 因子 | 边界 | 计划 id | 备注 |
| :--- | :--- | :--- | :--- | :--- | :--- |

| 试验 / run | 关键因子取值 | 主指标实测 | 方法 | 通过? |
| :--- | :--- | ---: | :--- | :---: |

叙述：因子选择逻辑、BayBE/AL 策略，**禁止编造未入库 run**。

## S5. 实验台账与测量
<!-- data:lab -->
| 时间 | 样本/item | 计划参数 | 实际参数 | 测量指标 | 值 | 方法 | 附件 |
| :--- | :--- | :--- | :--- | :--- | ---: | :--- | :--- |

来自 workbench / experiments / measurements；QC 报告挂附件链接。

## S6. 寻优、闭环与模型态
<!-- data:loop -->
| 闭环轮次 | 时刻 | rmse_by_metric | converged | doe_plan_id | 备注 |
| :--- | :--- | :--- | :---: | :--- | :--- |

| 候选配方 | 预测主指标 | 关键约束 | 版本/快照 |
| :--- | ---: | :--- | :--- |

叙述：收敛判断、偏差/漂移（bias-trend 若有）、下一步建议（软建议）。

## S7. 图表与多模态资产
<!-- data:artifacts -->
| 资产 | 类型 | 路径/URI | 关联节 | 生成自 |
| :--- | :--- | :--- | :--- | :--- |
| 盐雾对比 | plot_spec/png | artifacts/… | S5 | workbench |
| 结构式 | png | images/… | S2 | OCSR/附件 |

无法持久化的前端瞬时图：写入 **plot_spec JSON**（指标序列）供 Report 重绘，而不是丢空。

## S8. 开放问题 / Flag / 下一步
冲突、缺测、死链、待审 L2、建议补做 DOE。
```

### 3.3 节级数据块约定（Report 地基）

每个 `<!-- data:xxx -->` 后的 **第一张 Markdown 表** 为该节 canonical 表；Report 生成器优先解析表 + front-matter，叙述仅作补充。

可选增强（P4.2+）：在 front-matter 旁维护同构 JSON 文件  
`themes/project-{id}.data.json`（与 md 同 revision）——便于强类型 Report，而不强迫人读 JSON。

---

## 4. 重新评估：撰写与更新方式

### 4.1 原则

1. **骨架确定性，叙述可 LLM**  
2. **节级 patch，禁止无必要全量重写**  
3. **事件驱动 + 幂等**：同一事件重复到达不炸版  
4. **数字只来自结构化源**；LLM 不得改表内数值  
5. **Dossier 是 Report 的输入缓存**，不是第二证据库  

### 4.2 写入模式

| 模式 | 行为 | 适用 |
|------|------|------|
| `ensure` | 无则建空骨架（仅 front-matter + 空表头） | 项目创建 / 首次绑 campaign |
| `patch_section(S#)` | 只重编译该节表 + 可选重写该节叙述 | 日常事件 |
| `refresh_all` | 全节重装表；叙述按策略重写或保留 | 手动「重建卷宗」 |
| `annotate_llm(S#)` | 仅刷新叙述，不动表 | 用户点「润色」 |

默认自动化：**patch_section**，不是整页 LLM。

### 4.3 事件 → 节 映射（核心）

| 事件 | 现网钩子（现状） | Dossier 动作 |
|------|------------------|--------------|
| 项目创建 / 首次打开卷宗 | 无 | `ensure` 全骨架 |
| **技术要求保存** `PUT /api/projects`（requirement 变） | 无 wiki | **patch S1**（表全量替换自 Requirement/objectives/constraint_values）；可选 LLM 刷新 S1 叙述 |
| **文献检索入库 / ingest 完成** | L1 `compile_source` 已有 | **patch S2**（合并新 source_ids、主题簇行）；L1 照旧；可选抽机理句 |
| **SourceGuide / 产品抽取更新** | L1 materials/chemicals | **patch S3** 候选物料行 + `l1_paths` |
| **DOE 计划生成** `doe_plans` 写入 | 无 | **patch S4** 设计表 |
| **DOE/台账测量入库** experiments/measurements/workbench sync | 无 wiki；或有 auto_loop | **patch S4+S5**；指标行按 metric 对齐 S1 |
| **寻优完成** optimize task | 无 | **patch S6** 候选表；**patch S7** 若有 plot_spec |
| **闭环一轮** loop_iterate / auto_loop_on_sync | 写 `loop_history` | **patch S6** 历史行；必要时 S4 下一轮 DOE |
| **附件/QC/结构图** | attachments | **patch S7**（+ S5 附件列） |
| 手动「生成/刷新项目 Wiki」 | 仅 themes/system | `refresh_all` 或选节 |
| L2 体系综述编译 | `POST /themes/compile` | 不改 Dossier；S2/S8 可链到 theme path |

### 4.4 与「检索后能写 / 要求后能更 / DOE 后能更 / 闭环图能更」对齐

| 用户诉求 | 机制 |
|----------|------|
| 检索与 RAG 后能写 | ingest → L1 + Dossier S2 patch；Chat 仍双轨 |
| 输入技术要求后能更新 | project save hook → S1 patch（秒级、可无 LLM） |
| DOE 与实验台账有数据后可更新 | doe/workbench/measurements 钩子 → S4/S5 |
| 寻优与闭环分析数据与分析图可加入并更新 | loop/optimize 钩子 → S6/S7；图优先 plot_spec，有文件则挂路径 |

---

## 5. ContextPack（单一装配，Wiki 与 Report 共用）

`build_project_dossier_pack(project_id, *, campaign_id=None) -> DossierPack`

```text
DossierPack
├── meta: project_id, title, domain, campaign_ids, updated_at
├── requirements: Requirement + objectives + constraint_values + levers
├── literature: top sources/chunks (id, title, snippet, image_refs) + related L1 paths
├── formula: active_formulation / leaderboard[0] / formulation_versions
├── doe: doe_plans[] + factor bounds + linked runs
├── lab: experiments / workbench rows / measurements (typed)
├── loop: loop_history + latest OptimizationResult summary + rmse
├── artifacts: attachment paths + plot_specs + wiki image refs
└── flags: conflicts, missing metrics vs S1, stale sections
```

- Wiki `patch_section` **只消费 Pack 对应切片**  
- 未来 Report `briefing|feasibility|formula-compare|patent-memo` **同一 Pack**，换 Jinja/LLM 排版模板即可  

---

## 6. 图表与资产策略（为闭环/Report 打底）

| 类型 | 持久化策略 | S7 记法 |
|------|------------|---------|
| 实验附件 / QC PDF / SEM | `experiment_attachments` + 拷贝或软链到 `data/wiki/images|artifacts/` | 路径 + 关联 experiment_id |
| 文献内嵌图 | 解析阶段若抽出则入资产表；否则不虚构 | 仅保留真实路径 |
| 寻优/闭环曲线 | **优先**存 `plot_spec`（series, metric, round）于 Pack/JSON | Report/前端可重绘 |
| Pareto | `TradeOffAnalysis` 快照进 Pack（若 workspace 有） | 表 + 可选 spec |
| 结构式 | OCSR 临时图 → 晋升为附件才进 S7 | 未晋升不写死链 |

禁止：LLM 生成假图片路径。

---

## 7. 实施计划（P4 修订）

### 旗标

| Flag | 默认 | 含义 |
|------|------|------|
| `wiki_project_dossier_enabled` | **false** | 总开关 |
| `wiki_dossier_llm_narrative` | **false** | 允许节叙述 LLM；关则只更新表 |
| `wiki_dossier_auto_patch` | **false** | 事件自动 patch；关则仅手动 API |
| （已有）`wiki_compile_on_ingest` | true | 继续只驱动 **L1**；Dossier S2 另走 dossier patch |

### P4.0 契约（0.5–1d）

- [x] 定稿本文模板与 `section_revisions` schema；§10 四项已拍板  
- [x] ADR 附录：Dossier ≠ MkDocs；Dossier 为 Report 上游  
- [x] 旗标 `wiki_project_dossier_enabled` / `wiki_dossier_llm_narrative` / `wiki_dossier_auto_patch`（均默认 false）  
- [x] `THEME_TEMPLATES` 注册 `project_dossier`；`vertical_addendum` 插件钩子  
- [x] `*.data.json` 旁路双写契约  

### P4.1 DossierPack + 空骨架（1–2d）

- [x] `services/wiki/dossier_pack.py`（requirements 切片 + 空壳字段）  
- [x] `ensure_project_dossier(project_id)` 写空表头 + data.json  
- [x] API：`POST /dossier/ensure`、`GET /dossier/{project_id}`、`GET /dossier/{project_id}/pack`  
- [x] 单测：旗标门闩 + ensure 骨架 + data.json  
- [x] 后续：DOE/lab/loop 切片填实（P4.2）
### P4.2 节级确定性 patch（2–3d）

- [x] `patch_dossier_section(project_id, section, pack_slice)`  
- [x] 接线事件（均需 `wiki_dossier_auto_patch`）：  
  - project requirement 变更 → S1  
  - ingest 成功回调 → S2（增量 source_ids）  
  - doe_plan 保存 → S4  
  - workbench sync / measurements → S4/S5  
  - optimize/loop 完成 → S6 + plot_spec→S7  
- [x] 幂等：content_hash / section_revisions

### P4.3 LLM 叙述（可选，1–2d）

- [x] 按节 prompt；输入=该节表 + 有限 Raw 摘录  
- [x] 后校验：不得改表数字；不得新增无资产图  
- [x] 失败保留旧叙述

### P4.4 API / Hub（1–2d）

- [x] `POST /api/wiki/dossier/ensure`  
- [x] `POST /api/wiki/dossier/patch` `{project_id, sections?:["S1","S6"]}`  
- [x] `POST /api/wiki/dossier/refresh`  
- [x] Hub 项目旁「项目卷宗」入口；Reader 展示 `section_revisions` / 未审 Flag  
- [x] Chat 相关 Wiki 可命中 dossier（`themes/project-*` 检索加权）

### P4.5 Report 地基（并行预埋，0.5–1d）

- [x] 导出 `GET /api/wiki/dossier/{project_id}/pack`（即 DossierPack JSON）  
- [x] Hub Reports 占位页改为「基于卷宗生成（即将推出）」并标明依赖 pack  
- [ ] 不实现完整 Report 排版（留给 P5）

### P4.6 质量门

- [x] Hub 手测清单 + API/UI smoke：[`2026-09-14-wiki-hub-dossier-handtest.md`](./2026-09-14-wiki-hub-dossier-handtest.md) · `scripts/hub_dossier_handtest_smoke.py` · `frontend/scripts/hub_dossier_smoke.mjs`  
- [x] 单测：事件→节路由隔离（`test_event_section_matrix_routing` / `test_notify_doe_event_only_bumps_mapped_revisions`）+ Claims/DOE soft 回归  
- [x] 金样项目：要求→检索→DOE→台账→闭环→报告→导出（见 `tests/test_wiki_p5_export_e2e.py`）  
- [x] 文档：事件矩阵（本文 §4.3）+ 模板锚点冻结（§3）+ Hub 手测

**合计约 7–12 人日**（视事件接线面）。

---

## 8. P5（Report）如何吃这份 Wiki

| Report 模板（Hub） | 主读 Dossier 节 / Pack 切片 |
|--------------------|-----------------------------|
| briefing | S1 + S6 + S8 → requirements / loop / literature / flags |
| feasibility | S1 + S3 + S4 + S5 → requirements / formula / doe / lab |
| formula-compare | S3 + S6 → formula / loop |
| patent-memo | S2 → literature + source_ids |

实现记录：[`2026-09-14-wiki-p5-report.md`](./2026-09-14-wiki-p5-report.md) · 导出/Deck/金样：[`2026-09-14-wiki-p5-report-export.md`](./2026-09-14-wiki-p5-report-export.md)

Report **禁止**直接把 L2 叙述当 Claim；对外引用必须能点回 `source_ids` / 测量行。
MVP：确定性 Markdown + 可选 LLM 执行摘要；旗标 `wiki_dossier_report_enabled`。
P5.1：DOCX/PDF/PPTX 导出 + `deck` 幻灯模板 + 金样 E2E。

---

## 9. 非目标（P4）

- MkDocs 运行时  
- LLM 写 L1 数值  
- Claims 引用 Dossier 句  
- DOE 硬边界自动采纳 S6 建议  
- 完整 Report PDF/Word 排版（P5）  
- Compiler 内重跑 OCR  
- 第二向量库  

---

## 10. 拍板题（已确认 2026-09-14）

| # | 决策 | 结论 |
|---|------|------|
| 1 | Dossier 主键 | **`project_id`**（campaign 仅作可选关联字段） |
| 2 | 自动 patch | **`wiki_dossier_auto_patch` 默认 false**；先手动 API |
| 3 | `*.data.json` 旁路 | **P4 同步交付**（与 md 同 revision，供 Report） |
| 4 | 垂直行业 prompt | **可选 `vertical_addendum` 插件**（如 silane）；默认不加载 |

确认后从 **P4.0 → P4.2** 开工（本文已开工契约层）。

---

## 11. 决策摘要

| 决策 | 选择 |
|------|------|
| Wiki 主模板 | **`project_dossier` 八节锚定卷宗**，覆盖 FormuMind 全链路 |
| 主键 | **`project_id`** |
| 撰写方式 | 确定性表 + 可选 LLM 叙述；节级 patch |
| 更新方式 | 事件映射 S1–S7（自动默认关）；手动 ensure/patch/refresh |
| 机读旁路 | **`themes/project-{id}.data.json`** 与 md 双写 |
| 垂直 prompt | **`vertical_addendum` 可选** |
| 检索/RAG | 继续喂 L0/L1；Dossier S2 增量引用，不替代 Raw |
| Report | 同 DossierPack；P4 预埋 pack API，P5 做排版 |