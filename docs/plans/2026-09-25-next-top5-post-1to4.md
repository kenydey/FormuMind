# 下一步最值得升级的 Top-5（2026-09-25 · post #1–#4）

> **用户决策（2026-09-25）：#1–#5 全部暂不做**（仅保留为 backlog，不排期实现）  
> 基线：分支 `cursor/next-post-aprime-1to4-stale-trace-scan-softcorrect`（#1–#4 已交付；#5 Owner/KG 仪表上次跳过）  
> 已交付勿重复：topicality 真闸、归档/retention、scan 压力、Wiki embed/pagegraph、B–E、A′ 手工供应、stale→explain/替代、约束追踪、hybrid p50/p95+BM25 预筛、项目 soft-correct  
> 约束不变：不扩 Neo4j；不默认 auto TTL / 关 dual-write / 全局 auto_loop / auto_adopt / soft-correct；Claims/DOE 边界不破

## 代码证据（为何不是再开一遍旧单）

| 观察 | 证据 |
|------|------|
| 手工报价**未进成本预测** | `predictor._cost_and_sustainability` 仍 `RAW_MATERIALS.get(...).price_cny_per_kg`；`material_suppliers.price_cny_per_kg` 只进 `supply_flags` / 徽章 |
| 约束追踪已暴露「仅展示」洞 | `requirement_effect_trace`：`ph_target` / `film_weight_gsm` → `display_only`；用户看得见却未进评分 |
| Owner 硬校验后端已有 | `test_owner_phase2.py` + `assert_owner` 在 `MULTI_USER=true` 已 403；缺口在**产品化 UX / 列表过滤** |
| KG 关系仍偏暗路径 | `kg_relations_on_ingest=False`；DependencyManager 有「补语义关系」，quality-ops **无** measured vs llm 比例 |
| hybrid ANN 仅一阶段 | BM25 预筛已上；无持久化向量索引；p95 热时仍全表 tokenize |
| dual-write 仍默认开 | `materials_suppliers_json_dual_write=True`（刻意兼容窗） |

---

## #1 — 手工报价进 `cost_cny_per_kg`（供应数据真正改分）

**现状**：A′+#1 能存价、能标 stale；成本分仍用目录工程估价。

**做**：
- `predictor` 解析材料时：非 stale 的 `material_suppliers.price_cny_per_kg`（或 hydrated JSON）优先于 catalog；stale/缺价回退 catalog 并在 explain 记 `price_source`
- 多供应商：取中位或「主供应商」；不把 stale 价当硬事实
- 单测：改手工价 → `predicted.cost_cny_per_kg` 与 score 变化；stale 不覆盖 catalog

**价值**：供应主数据闭环从「标风险」到「动排序」。  
**不做**：爬价；默认全局改权重曲线。

---

## #2 — 把 `display_only` brief 字段接进评分/软约束

**现状**：effect_trace 已标 `film_weight_gsm` / `ph_target` 仅展示；长期「填了不管用」的信任债还在。

**做**（小步、可开关）：
- `film_weight_gsm` / `coating_weight_gsm`：有值时同步/校验对应 objective 或 soft miss（进 `constraints_miss`）
- `ph_target`：配方/工艺预测有 `ph_value` 时做 soft band；无预测则保持 display_only 并写清原因
- UI：约束追踪芯片可点「升为 objective / 保持仅展示」

**价值**：#2 追踪面板从审计变成改进行动。  
**不做**：重写 Intent LLM；强行虚构无预测指标。

---

## #3 — KG 关系主航道可观测（measured vs llm）

**现状**：入库关系默认关；rebuild 藏在依赖面板；推荐已用 KG soft score，运维看不见关系库存健康。

**做**：
- quality-ops / Hub：关系边计数、`extraction_method` 分布（measured / rule / llm）、空关系 CTA→「补语义关系」
- KgRelationPanel / Hub 统一「最近 rebuild 状态」
- 仍默认 `kg_relations_on_ingest=False`

**价值**：上次跳过的 #5b；不扩 Neo4j 也能让主航道关系层可运营。  
**不做**：默认开入库 LLM 抽关系；上 Neo4j。

---

## #4 — Owner Phase 2 产品化（后端已硬，补前端与列表）

**现状**：`FORMUMIND_MULTI_USER=true` 时 campaign/task 越权已 403（`test_owner_phase2`）；ApiAccessPanel 仅有一句提示；项目/列表过滤与身份切换 UX 不足。

**做**：
- Settings / 顶栏展示 `GET /api/auth/status` 的 `owner` + multi_user
- campaign / project 列表按 owner 过滤（`NULL` 仍公共）
- 简短「多 token 配置」指引（`API_TOKENS_JSON`）

**价值**：多团队同实例时的真实边界可感知、可操作。  
**不做**：改默认单用户行为；强制迁移历史 `owner_id`。

---

## #5 — 兼容窗收口：dual-write 可关 + hybrid 二阶段（按闸）

**现状**：归一化表已是读路径真相；JSON 双写仍默认 True；#3 仅 BM25 预筛。

**做**（两刀可拆，同属「触阈再动」）：
1. Settings / 项目级 **显式**关闭 `materials_suppliers_json_dual_write`（确认文案 + 回读自 link 表）；不默认关  
2. ~~当 `ann_last` 连续为真或 p95 仍超阈：对 embed 子集建进程内矩阵/简易 ANN（仍不引 Qdrant）~~  
   **已交付（Option A，2026-09-26）**：`kb_hybrid_ann_matrix_min_dim` + sticky hysteresis；`ann_matrix_last` / `ann_streak` 进 quality-ops；`FORMUMIND_EMBEDDING_MODEL` 目录含 Qwen3-Embedding（换后必重建）。**仍不做 Qdrant compose。**

**价值**：减双写债 + 检索在压力下再挤一档，且都有闸。  
**不做**：未确认就全局停 dual-write；默认上外部向量库。

---

## 明确不进本轮 Top-5

| 项 | 原因 |
|----|------|
| 再做 stale 徽章 / effect_trace 骨架 / 项目 soft-correct / p50 埋点 | 刚交付 |
| 默认开 auto_loop / auto_adopt / soft-correct / relations_on_ingest | 产品约束 |
| 立刻 Qdrant / Neo4j | 未到度量阈值；与约束冲突 |
| 重写 Intent→Requirement LLM | #2 接线优先于生成质量 |

## 建议落地顺序

```
#1 报价→cost  →  #2 display_only 接线
                      ↘ #3 KG 关系仪表（运维可见）
#4 Owner UX（有多用户日程再做）
#5 dual-write 可关 + hybrid 二阶段（有压力/兼容窗到期再做）
```
