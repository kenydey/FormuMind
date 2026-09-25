# FormuMind 代码分析复核 + 下一步升级计划（2026-09-25）

> 对照对象：另一份「本地代码全面分析」报告（HEAD 声明 `19a1802`）  
> 本仓库事实基线：`main @ 19a1802`，工作区干净，远程仅 `origin/main`  
> 原则：**只认代码与已合入 plan**；与近期 W1–W5 / Top-5′″‴ 冲突处以 `config.py` / `docs/plans/2026-09-25-*.md` 为准

---

## 0. 规模与事实基础（复核）

| 指标 | 对方报告 | 本机复核 | 判定 |
|------|----------|----------|------|
| HEAD | `19a1802` | `19a1802fde69d71…` | ✅ |
| `backend/app` `.py` | 311 / ~76,875 | 311 / 76,873 | ✅ |
| `frontend/src` ts/tsx/css | 211 / ~42,586 | 211 / 42,585 | ✅ |
| API 模块 | 33 | 33 | ✅ |
| services `.py` | 172 | 172 | ✅ |
| 测试文件 | 587 | ~347（`test_*.py` + `*.test.ts(x)`，排除 node_modules） | ⚠️ 计数口径偏宽；量级「测试很多」结论仍成立 |
| SQLAlchemy `Base` 模型 | ~26 | `models.py` 内 25 个 `class *(Base)` | ✅ 量级对 |

产品定位（README 主闭环）与「三条主路径」叙事：**同意**，与 `README.md:5-19` 一致。

---

## 1. 总评：对方哪里对、哪里过时、哪里臆断

### 1.1 成立且应采纳的判断

1. **产品不是单点工具，而是「知识→材料→配方→DOE→实测→再推荐」闭环** — 与 README / LabWorkbench / campaign `loop_history` 一致。  
2. **主路径收敛**优于继续堆 Experimental 能力 — 与近期「不扩 Neo4j、不默认 auto_*」约束同向。  
3. **材料/供应商仍非可决策主数据** — `SupplierRow` 只有 `name/url`；`MaterialSupplierRow` 只有 `product_url/source`；**无** country / MOQ / price_source / price_updated_at（见 `models.py` Material/Supplier 段）。  
4. **推荐解释不完整** — 排行榜有 score、实测 banner、不相容一键替代（`FormulaLeaderboard.tsx`），但**没有**统一的「约束命中 / 文献 vs 结构相似 / 供应风险 / 不确定性」拆解面板。  
5. **持久化 KB 的 `hybrid_search` 是应用层扫 chunk** — `kb_search_scan_limit=5000` + Python BM25 + cosine 循环（`hybrid_search.py`）；与会话级 `rag.BM25FAISSStore` **不是同一条路径**（README 写 FAISS 易误导）。  
6. **「可用 ≠ 生产可信」** — `degrade_return` / empirical surrogate / rule fallback 大量存在；产品层标注仍不足。

### 1.2 必须纠正：把「已落地」写成「P0 待做」

| 对方主张 | 代码事实 | 结论 |
|----------|----------|------|
| P0「topicality 正式门控」 | `kb_relevance_shadow: bool = False`（`config.py`）；注释写明 Top-5 #1 已翻转为真闸；plan `2026-09-25-next-top5-rollout.md` | ❌ **过时**。下一刀不是「开闸」，是闸效果可观测与误杀校准 |
| 「topicality 曾影子模式」暗示仍是现状 | 默认已 False；shadow 路径仅校准/回滚（`kb_ingest.py` + `/relevance-shadow/stats`） | ⚠️ 历史正确，**现状描述错误** |
| P0「归档和生命周期」待建 | W3 soft-archive + W4 `POST /api/kb/retention/purge`（默认 dry-run）+ Hub 运维 UX | ❌ 基础设施已在；缺的是**常态化运营指标**而非绿野建设 |
| 「全文可用性硬过滤」待建 | OpenAlex 路径已 `is_oa` 强制过滤（`search_providers.py` ~390）；专利走 landing HTML；失败入库记 reason | ⚠️ **部分已做**；缺跨源「全文成功率仪表」与统一拒收策略说明 |
| 「Wiki page graph / embed 仍偏关」 | `wiki_page_graph_enabled=True`、`wiki_embed_enabled=True`（Top-5‴） | ❌ 过时 |
| P0「DOE 闭环真正自动化」像从零建设 | `workbench_loop.dispatch_loop_after_sync`、`loop_convergence_*`、`loop_history`、LoopModal retry/auto_adopt、前端 `autoLoopOnSync` 链**均已存在**；默认 `auto_loop_on_sync=False` / `auto_adopt_next_doe_on_loop=False` 是**产品约束** | ⚠️ 表述夸大。真实缺口是：**项目级可控开启 + 端到端验收脚本覆盖 campaign→train→next DOE→停**，不是缺 dispatcher |
| 「统一 evidence contract 从零」 | 已有 `Evidence`、`grounding_confidence`、`rationale`、KG `extraction_method`、Wiki `filter_raw_evidence`（Claims 只认 Raw） | ⚠️ 缺的是**跨材料/供应/实验/模型预测的统一展示契约**，不是无证据对象 |
| 测试「587」 | 本机同类 glob ~347 | 次要；勿用虚高数字做决策 |

### 1.3 对方低估或漏掉的近期护栏（必须保留）

近期合入明确 **不做 / 默认关**：

- 不默认开 `auto_loop_on_sync` / `auto_adopt_next_doe_on_loop`
- 不默认 TTL 物理删库；`materials_suppliers_json_dual_write` 默认仍 True
- 不扩 Neo4j 为主存储；`neo4j_enabled=False`；`kg_relations_on_ingest=False`
- Claims / DOE 边界：Wiki/STORM/dossier = draft，`wiki/retrieve.py:filter_raw_evidence`

任何「下一步」若要求默认打开上述开关，视为与现行产品策略冲突，需单独论证。

---

## 2. 分能力复核（精简，只标争议点）

| 能力 | 对方判断 | 本评 |
|------|----------|------|
| Intent → Requirement | 已实现，缺可追踪性 | ✅ 同意；缺「字段→推荐/DOE 是否生效」审计 UI |
| 多源检索 + 全文 | 较完整 | ✅；OpenAlex OA 闸已存在 |
| 持久化 hybrid RAG | 可用平台级 | ✅；瓶颈在 scan，且 **已有** `scan_pressure`/`scan_near_cap`（`kb_index` stats）— 对方 P2「先测量」半过时 |
| KG | 实体/关系基建完整，高阶默认关 | ✅；勿把 Neo4j 当主路径 |
| 材料库 | 可持久化目录 | ✅；供应决策字段缺口真实 |
| 推荐 | 多证据候选，非工业决策系统 | ✅；解释层不足 |
| DOE 闭环 | 「最有战略价值 + 短板」 | ✅ 战略对；短板应写「默认关 + E2E 金样不足」，不是「没有自动训练/下一轮」 |
| Wiki | 第二大脑，不替 Raw | ✅；Claims 隔离代码明确 |

---

## 3. 下一步升级计划（纠正后的 Top-5）

约束沿用：不扩 Neo4j；不默认 auto TTL / 关 dual-write / 全局 auto_loop / auto_adopt / soft-correct；Claims/DOE 边界不破。

### #1 — 供应决策主数据（真正的 P0）

**为什么排第一**：对方材料 P0 方向对，且与 intentional 延迟的 dual-write 停写可衔接；当前 schema 硬缺口可证伪。

**做**：

1. 扩展 `suppliers` / `material_suppliers`（或旁路 `supplier_quotes`）：`country`、`currency`、`price_cny_per_kg`、`price_source`（manual|import|scrape）、`price_observed_at`、`moq`、`pack_size`、`lead_time_days`（链路级，不仅材料级）。  
2. MaterialsPanel：供应商行可编辑上述字段；展示「价格年龄」与来源徽章。  
3. 替代排序注入：缺价/过期价降权或标 `stale_price`（默认只标记，不静默改分权重过大）。  
4. 仍默认 dual-write；新增字段只写归一化表。

**不做**：自动爬价默认开；删 `suppliers_json` 列。

**验收**：迁移 + API round-trip 单测；MaterialsPanel 手测；替代列表可见 stale 标记。

---

### #2 — 项目级闭环「可控自动」+ DOE 金样 E2E

**为什么**：骨架已在 `workbench_loop.py`；缺的是与 `wiki_dossier_auto_patch` 同形态的**项目级开关 + 确认文案 + 金样脚本**。

**做**：

1. `ProjectWorkspace.auto_loop_on_sync`（已有字段痕迹）与 UI 勾选对齐全局 flag：项目开 OR 全局开才 dispatch；写入前提示「将触发训练/下一轮 DOE」。  
2. Campaign 状态机表面化：running / converged / paused / failed + retry（W5 已有失败 retry，接到状态条）。  
3. 新脚本 `scripts/golden_doe_loop_e2e.py`（或扩展现有 worker 单测）：create campaign → 写入 measured → sync →（trigger_loop=true）→ 断言 `loop_history` 增轮、`next_doe`/`doe_plan`、收敛后不再 dispatch。  
4. Hub/Workbench 展示最近 round RMSE + converged（部分已有，统一入口）。

**不做**：全局默认 `auto_loop_on_sync=True`；静默 `auto_adopt`。

**验收**：pytest + 可选 live smoke；收敛后 `dispatch_loop_after_sync` 返回收敛文案且无新 task。

---

### #3 — 推荐「证据×约束×风险」解释条（产品化）

**为什么**：`kg_recommend_score` / `grounding_confidence` / `rationale` 后端已有碎片；Leaderboard 只露出分数与实测 banner。

**做**：

1. 后端：每个 `Formulation` 附 `explain` 结构化块：`objectives_hit[]`、`constraints_miss[]`、`evidence_refs[]`（source_type/id）、`kg_signals`（measured/inhibit/synergy）、`supply_flags`、`uncertainty`（复用 `recommend_uncertainty_flag`）。  
2. 前端：Formula 卡展开「为何推荐」折叠面板；禁止只显示裸分。  
3. 与 soft-correct：若开启，解释条注明 `bias_corrected`（默认仍关）。

**不做**：统一重写全库 Evidence 表结构；不改 Claims 证据源。

**验收**：单测 explain 字段稳定；UI 快照/组件测。

---

### #4 — KB 闸与质量「运营面板」（不是再造闸）

**为什么**：真闸/归档/retention/scan 压力已在；缺用户可懂的质量面板（对方「质量评估一级能力」方向对，但起点应接现有 API）。

**做**：

1. Hub 依赖/KB 面板聚合：`relevance-shadow/stats`（回滚用）、近期 `low_topicality` 拒收率、`sources_active/archived`、`scan_pressure`、全文 fetch 失败率（从 ingest audit 抽）。  
2. 每项目粗分：`kb_quality_score`（启发式：全文率、主题拒收率、archived 比）只读展示。  
3. Golden MRR/Recall 趋势（Top-5‴ Hub 探针）与上述并排。

**不做**：再 flip `kb_relevance_shadow`；默认自动 purge。

**验收**：stats API 契约测 + Hub 面板手测。

---

### #5 — 三条主路径「轻向导」（收敛 UX，不新造引擎）

**为什么**：对方路径 A/B/C 正确；现 UI 能力散落 Chat / Hub / Materials / Workbench。

**做**：

1. 入口三张卡：新配方 / 材料替代 / 项目知识 — 只做**步骤编排 + 深链已有面板**，不复制业务。  
2. 每步展示当前旗标成熟度：从 `FLAG_REGISTRY` 派生 `stable|beta|experimental`（新增可选 `maturity` 字段，默认 stable）。  
3. 空态 CTA 复用 Top-5‴ leaderboard/pagegraph 模式。

**不做**：第四条「STORM/Neo4j/OCSR」主路径；大改信息架构。

**验收**：组件测路由；手测三路径可走通到已有完成态。

---

## 4. 明确降级 / 延后（相对对方排序）

| 对方项 | 本计划 | 理由 |
|--------|--------|------|
| P0 topicality 正式门控 | 降为运维观测（#4） | 已默认真闸 |
| P0 全文硬过滤（笼统） | 并入 #4 仪表 + 按源补洞 | OpenAlex 已硬滤 |
| P0 DOE「从零自动化」 | 改为 #2 可控自动 + E2E | dispatcher 已存在 |
| P1 统一 evidence contract（大一统 schema） | 先做 #3 展示契约 | 避免大迁移 |
| P2 立刻上 Qdrant/pgvector | **延后**，以 `scan_pressure` 触发 | 未到阈值先测再上；且会话 RAG 已有 FAISS 旁路 |
| P2 功能成熟度标签 | 挂在 #5 轻量做 | 同意方向 |

---

## 5. 建议落地顺序（执行批次）

```
Batch A  #1 供应决策字段 + UI + 替代 stale 标记
Batch B  #2 项目级 auto_loop 确认 + DOE E2E 金样
Batch C  #3 推荐 explain 结构化 + Formula 卡
Batch D  #4 KB 质量运营面板（接现有 stats）
Batch E  #5 三路径轻向导 + flag maturity
```

每批：plan 小节 → 实现 → pytest/vitest → 手测 artifact → 合 `main` → 删功能分支。

---

## 6. 一句话

对方报告的**产品定位与「少堆功能、抓质量/供应/闭环/解释」方向成立**；但其 **P0 清单严重滞后于 `19a1802` 已合入的 W1–W5 / Top-5 真闸、归档、retention、scan 压力、Wiki embed/pagegraph、闭环骨架**。  
下一阶段应以 **供应可决策数据 → 项目级可控闭环金样 → 推荐可解释 → KB 运营面板 → 主路径向导** 推进，而不是重复「打开 topicality」或「从零做 DOE 自动」。
