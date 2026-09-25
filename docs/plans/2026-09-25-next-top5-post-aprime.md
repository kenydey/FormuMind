# 下一步最值得升级的 Top-5（2026-09-25 · post A′）

> 基线：`main @ 6374a50`  
> 已交付勿重复：topicality 真闸、归档/retention、scan 压力、Wiki embed/pagegraph、B–E 闭环/explain/质量面板/向导、A′ 手工供应字段  
> 约束不变：不扩 Neo4j；不默认 auto TTL / 关 dual-write / 全局 auto_loop / auto_adopt / soft-correct；Claims/DOE 边界不破

## #1 — 把 `stale_price` 接进推荐解释与替代排序（真用上供应数据）

**现状**：A′ 已写归一化表并在 MaterialsPanel 显示 `stale_price`，但**未进入** `formulation_explain.supply_flags` / 替代排序（全库仅 materials + normalize 引用）。

**做**：
- explain 组装时读取配方材料的 `suppliers_json`/`material_suppliers`，注入 `supply_flags`（缺价 / stale / 交期过长）
- `substitution` / leaderboard 替代候选增加 `stale_price` 降权或徽章（默认只标不狠扣）
- FormulaExplainPanel 已有供应区，接真数据即可

**价值**：手工供应字段从「能存」变成「能决策」。  
**不做**：爬价；默认大改分。

---

## #2 — 需求字段 → 推荐/DOE「生效追踪」面板

**现状**：Intent→Requirement 有 schema；explain 有 `objectives_hit/constraints_miss`；缺「哪个 brief 字段实际进了评分/DOE 因子」的可点击审计。

**做**：
- 后端：`Requirement` 生效清单（objectives / levers / constraints 哪些被 `recommend_pipeline` / DOE 消费）
- 前端：技术需求旁或 Formula 卡「约束追踪」：命中 / 未接线 / 仅展示
- 单测：改一个 objective → explain.hit 变化

**价值**：回应长期「结构化了但未真正生效」的信任问题。  
**不做**：重写 intent LLM。

---

## #3 — 持久化 KB 检索扩容（以 `scan_pressure` 为闸）

**现状**：`hybrid_search` 仍应用层扫 ≤5000 chunk；Hub 已有 `scan_pressure` / near-cap CTA；会话 RAG 另有 FAISS，**不是**同一路径。

**做**（分阶段）：
1. 先埋 p50/p95 检索耗时到 quality-ops  
2. `scan_near_cap` 或 p95 超阈时：项目级 embedding ANN（sqlite 向量或现有 embed 缓存）+ BM25 候选预筛，**不**默认上 Qdrant  
3. 文档写清「持久化 hybrid ≠ rag.BM25FAISSStore」

**价值**：数据涨了才不会静默变慢。  
**不做**：未触阈值就引新向量库。

---

## #4 — 项目级 `prediction_bias_soft_correct`（对齐 auto_patch / auto_loop）

**现状**：全局 `prediction_bias_soft_correct=False`；explain 已能标 `bias_corrected`；台账有 bias trend。

**做**：
- `ProjectWorkspace` 项目开关 OR 全局（默认仍关）
- Workbench 勾选 + 确认文案（会改 predicted，不改 measured）
- Leaderboard / explain 统一「已校准」徽章

**价值**：闭环飞轮最后一公里，且不强迫全局开。  
**不做**：默认全局 True；静默改台账。

---

## #5 — Owner Phase 2 多用户硬隔离（或并列：KG 关系主航道可观测）

**主选 #5a Owner Phase 2**  
**现状**：`assert_owner` / `FORMUMIND_MULTI_USER` 已预埋，默认恒 `default` no-op（`api_auth.py`）。

**做**：MULTI_USER=true 时 token→owner 强校验；前端展示当前 owner；旧 `owner_id IS NULL` 仍公共。

**价值**：多团队共用同一实例时的真实安全边界。

**备选 #5b**（若短期无多用户需求）：KG `relations/rebuild` 主航道运维 UX + measured vs llm 关系比例仪表（仍默认 `kg_relations_on_ingest=False`）。

---

## 明确不进本轮 Top-5

| 项 | 原因 |
|----|------|
| 再开 topicality / 默认 purge | 已交付 |
| 默认开 auto_loop / auto_adopt / soft-correct 全局 | 产品约束 |
| 立刻 Qdrant/Neo4j | 未到度量阈值；与现行约束冲突 |
| 停 `suppliers_json` dual-write | 仍需兼容窗，单独批次 |

## 建议落地顺序

```
#1 stale→explain/替代 → #2 约束追踪 → #4 项目级 soft-correct
                              ↘ #3 scan 扩容（有压力再做）
#5 Owner Phase2（有多用户日程）或 KG 关系仪表
```
