# 现网主航道：灰度卷宗/Report × 配方/KG 飞轮

状态：**启动（2026-09-22）** — `llm_wiki` 借刀已停表并清除本地克隆  
前置：金样闭环 #133 · Workbench→S5 #134 · KG 材料回流 / 指标排序 / 榜单徽标 #106–#108  

---

## 0. 航道定义

| 航道 | 目标 | 不做 |
|------|------|------|
| **灰度 Wiki** | 旗标开后：卷宗五表有料 → Report MD → S4 草稿进 S8；Claims/DOE 不破窗 | 再开 llm_wiki S6/S7 |
| **配方 / KG** | Workbench 实测 → `ExperimentRow`/S5 → KG measured 回流 → 推荐软排序可观测 | Neo4j 双写；LLM 洗 L1 |

本地 `vendor/llm_wiki`：**已删除**（2026-09-22）。

---

## 1. 自动化门禁（CI / 本地优先）

```bash
cd backend && python -m pytest -q \
  tests/test_grayscale_kg_maintrack_gate.py \
  tests/test_wiki_grayscale_gate.py \
  tests/test_wiki_workbench_lab_dossier_s5.py \
  tests/test_kg_measured_bonus.py \
  tests/test_kg_provenance.py
```

活栈（Vite→API）：

```bash
python3 scripts/grayscale_kg_maintrack_smoke.py
# 可选：接金样 / Workbench
python3 scripts/golden_rd_loop_smoke.py
python3 scripts/workbench_dossier_s5_smoke.py   # ELN 可达时
```

---

## 2. 灰度手测清单（真人项目）

| # | 步骤 | 期望 |
|---|------|------|
| G1 | Settings 开 `wiki_enabled` / `wiki_project_dossier_enabled` / `wiki_dossier_report_enabled` / `wiki_chat_save_draft` | Hub Reports 徽标非「预留」 |
| G2 | 活动项目 → Wiki「项目卷宗」→ 刷新 | S1–S6 有表；S5 优先 `experiment:*` |
| G3 | Reports → briefing → 生成 → 导出 MD | `draft_not_claims`；MD 可下 |
| G4 | Chat 存 Wiki 草稿 | `queries/` + S8 待审；Claims 无 wiki: |
| G5 | Workbench sync Completed 测量 | 提示含 `KG 回流 N 条`（若有）；`GET /api/kg/feedback/stats` 的 `measured_material` 可增；Workbench / Hub 图有 `kg-feedback-stats-strip` |
| G6 | 再跑推荐（chem_screen） | 榜卡**折叠头**可见 `card-measured-chip`；展开区仍有指标感知条 |

---

## 3. 验收签字

- [x] 删除 `vendor/llm_wiki`
- [x] 借刀计划标注克隆清除 + 主航道切换
- [x] `test_grayscale_kg_maintrack_gate.py` 锁灰度 dossier/report + KG stats
- [x] `scripts/grayscale_kg_maintrack_smoke.py` 活栈冒烟
- [x] **代码侧 G5/G6 可观测**：`2026-09-23-kg-measured-observability.md`（tip / strip / 折叠芯片）
- [ ] 真人项目勾完 G1–G6（运营）
