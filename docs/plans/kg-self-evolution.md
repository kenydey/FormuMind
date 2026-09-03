# P0 实施计划：KG 自进化闭环（实测 → KG 回流）

> 基于代码调研的细化实施计划。优先级方案见 `docs/plans/next-upgrade-priorities.md`（已批准）。
> 本文为 P0 的文件级可执行计划，待确认后实施。

## 1. 调研结论（代码证据）

| 项 | 证据 | 结论 |
|----|------|------|
| KG 只读现状 | `domain/knowledge.py` 仅 `baseline_formulation`/`variant_formulations`/`offline_recommend_fallback`（读/模板）；`active_learning.py:43` 只 `knowledge.baseline_formulation` | ✅ 确认 KG 当前单向只读 |
| KG 写入接口已存在 | `db/entity_store.py`: `upsert_entity`(83) / `add_link`(187) / `merge_semantic_link`(231)，均接受 `evidence_refs`（`source_id`/`chunk_id`/`sentence`/`confidence`/`extraction_method`） | ✅ 写入通道成熟，**无需新建存储** |
| evidence 累积机制 | `merge_semantic_link._merge`(246-256) 追加 `evidence_ref` 到现有关系 | ✅ 实测证据可累积到文献关系上 |
| 闭环触发点 | `services/workbench_loop.py: dispatch_loop_after_sync`(71) 在 sync 后调用；`api/experiments.py:341` sync 处理完调 `auto_loop` | ✅ 有稳定挂接点 |
| 最优配方+实测值可得 | `campaign_store._append_loop_history`(226) 收 `loop_history`（含 doe_plan_id/收敛分析）；`campaign.rows` 含实测 `measured` 值 | ✅ 数据可及 |

**核心判断**：P0 不是"从零建存储"，而是**在闭环收敛点调用现有 `merge_semantic_link` 把实测证据作为 `extraction_method="measured"` 的 evidence_ref 写回相关 KG 关系**。风险中低（复用现有写入 + provenance 分隔）。

## 2. 设计

### 2.1 provenance 分隔（防污染基线）
- 实测证据 `evidence_ref` 固定：`extraction_method="measured"`，`source_id=f"measured:campaign_{campaign_id}:round_{n}"`
- 文献证据 `extraction_method` ∈ {rule, llm, ner, ...}（现有）
- KG 查询/排序时按 `extraction_method` 区分权重（后续可做，本期仅写入，不改动读取权重）

### 2.2 回流内容映射
对每轮收敛分析中"最优候选"的实测结果，写回两类关系：
1. **组分→性能**：`link_type="measured_performance"`，`src=组分实体`（如 `epoxy_acrylic_emulsion`），`dst=性能属性实体`（如 `salt_spray_resistance`），evidence 记实测值
2. **配方→可行性**：`link_type="measured_feasibility"`，记录实测是否满足约束（复用三期 `kg_compat` 逻辑）

实体解析复用现有 `entity_resolver`（`services/kg/entity_resolver.py`）把组分名/性能名映射到 `KGEntity.id`。

### 2.3 触发点
在 `api/experiments.py` 的 sync 处理（341 行附近 `dispatch_loop_after_sync` 调用处）**之前**，插入：
```python
if settings.kg_measured_feedback_enabled:
    from ..services import kg_feedback
    kg_feedback.ingest_measured_evidence(campaign_id, measured_rows)
```
新增服务 `services/kg_feedback.py: ingest_measured_evidence(campaign_id, rows)` 封装实体解析 + `merge_semantic_link` 调用。

## 3. 文件变更清单

| # | 文件 | 改动 |
|---|------|------|
| A1 | `backend/app/services/kg_feedback.py` | **新建**：`ingest_measured_evidence(campaign_id, rows)` — 解析实体、调 `entity_store.merge_semantic_link` 写 measured evidence |
| A2 | `backend/app/api/experiments.py` | sync 处理处（~341）插入 `kg_feedback.ingest_measured_evidence` 调用（受 `settings.kg_measured_feedback_enabled` 门控） |
| A3 | `backend/app/config.py` | 新增 `kg_measured_feedback_enabled: bool = True` |
| A4 | `backend/app/db/entity_store.py` | （可选）`merge_semantic_link` 已支持，无需改；若需 `measured_performance` link_type 校验则扩 `SEMANTIC_LINK_TYPES`（在 `db/entity_store.py` 顶部） |
| A5 | `backend/tests/test_kg_feedback.py` | **新建**：构造 campaign + measured rows，断言 KG 出现 `extraction_method="measured"` 的 link；断言不污染文献 evidence（原 evidence_refs 保留） |

## 4. 实施步骤

1. A3 config 加开关（默认开）
2. A1 新建 `kg_feedback.ingest_measured_evidence`：
   - 取 campaign 的 `lever_snapshot`（组分）+ `rows` 的实测性能
   - 对每个组分名调 `entity_resolver.resolve(name)` → entity_id
   - 对每个性能指标调 `entity_resolver.resolve(metric)` → entity_id
   - `merge_semantic_link(src=组分, dst=性能, link_type="measured_performance", confidence=归一化实测值, evidence_ref={source_id, extraction_method:"measured", sentence:f"实测 {metric}={val}"})`
3. A2 在 experiments.py sync 处挂接（门控）
4. A4 确认 `measured_performance`/`measured_feasibility` 在 SEMANTIC_LINK_TYPES（否则补）
5. A5 测试
6. tsc / pytest 回归

## 5. 风险矩阵

| 风险 | 缓解 |
|------|------|
| 实测证据污染基线 KG（误导后续推荐） | `extraction_method="measured"` 分隔；本期只写不读权重变更；A5 断言原 evidence 保留 |
| 实体解析失败（组分名不在 KG） | `resolve` 返回 None 时跳过该关系，记 warning，不中断 |
| 闭环频繁写 KG 性能退化 | `kg_measured_feedback_enabled` 开关 + 仅收敛轮写入（非每行） |
| link_type 未登记导致写入被拒 | A4 确认/补 SEMANTIC_LINK_TYPES |

## 6. 验证

- 单测：构造 campaign + 实测 rows → `ingest_measured_evidence` → 查 `entity_store` 该关系 `evidence_refs` 含 `extraction_method="measured"` 且原有文献 evidence 仍在
- 集成：跑一次逆设计→验证 DOE 下发→台账填实测→sync→确认 KG 出现 measured link
- 回归：核心 pytest 无退化；前端 tsc 零错误

---
*本计划全部基于 `backend/app` 现有代码（entity_store / workbench_loop / knowledge / experiments）调研，未编造接口。*
