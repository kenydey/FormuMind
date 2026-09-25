# Next Top-5′：G1–G6 收口 · STORM 灰度 · auto_patch 安全子集 · KG 关系异步补齐

> 状态：**已实现**（2026-09-25）  
> 前置：`main @ b08ab02`（Top-5 真闸/deny/卷宗默认开）  
> 分支：`cursor/next-top5-g1-storm-autopatch-kg-rel`  
> 批项：**#1–#4**（#5 Owner Phase 2 本轮不做）

## 0. 本批做 / 不做

| # | 做 | 不做 |
|---|----|------|
| 1 | 扩展灰度冒烟覆盖 G1–G4 自动化证据；修明显空态；文档勾选自动化可证项 | 真人运营签字替代；默认开 auto_patch |
| 2 | `wiki_storm_report_enabled` **默认 True**；Hub 文案强调 draft_not_claims + section 帽 | 默认开 `wiki_storm_parallel`；改同步短 Report |
| 3 | auto_patch **仍默认关**；仅 `_EVENT_SECTIONS` 白名单事件可触发；未知事件 skip（不再全量 8 节） | 默认开 auto_patch；LLM 洗表格 |
| 4 | `rebuild_relations` 不依赖 `kg_relation_extract_enabled`（入库仍关）；API `limit` 帽；stats/UI 提示可点 | 默认同步 `kg_relations_on_ingest`；Neo4j |

## 1. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_wiki_p4_dossier.py \
  tests/test_wiki_storm_report.py \
  tests/test_top5_rollout_defaults.py \
  tests/test_kg_relation_extractor.py \
  tests/test_grayscale_kg_maintrack_gate.py
python3 scripts/grayscale_kg_maintrack_smoke.py
```
