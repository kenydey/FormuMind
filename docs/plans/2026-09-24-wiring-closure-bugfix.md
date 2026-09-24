# 接线闭合 + Bug 修复计划（2026-09-24）

> 状态：**已实现** · 基线 `main @ ea49d8f`（#139） · 分支 `cursor/wiring-closure-bugfix`  
> 方法：OpenAPI 路由 × `api.ts` × 组件调用交叉；灰度/KG/Workbench 主航道缺陷复核

## 0. 结论摘要

| 类 | 数量 | 处置 |
|----|------|------|
| P0 半接线 / 关键缺陷 | 3 | 本 PR 必修 |
| P1 接线 + 可观测缺口 | 5 | 本 PR 必修 |
| P2 增强 / 清理 | 若干 | 本 PR 能合则合；其余记债 |

不改 Claims/DOE 硬边界；不双写 Neo4j；不关产品默认 ELN 硬依赖（无 Docker 环境另议）。

---

## 1. P0 — 必修

| # | 问题 | 证据 | 修复 |
|---|------|------|------|
| W1 | Chat `sourced_claims` / `clarification` SSE 已发、FE 丢弃 | `searchSlice` `done` 只写 content/citations；旗标默认开 | ChatMessage 扩展 + ResearchPanel 核验条 / 澄清按钮 |
| B1 | Datalab 跨 campaign 搜索 `campaign_id=0`/`row_id=0` | `_parse_datalab_search` 硬编码 0 | 用本地 `sample_refs` / `formumind_c{N}_r{M}_*` 映射 |
| B2 | `GET /kg/feedback/report` `recent_bias` 常空 | 只看 history 末条 `rmse_by_metric`；sync 写的是 `prediction_bias.by_metric` | 扫描 history；兼容 `by_metric.*.rmse` |

---

## 2. P1 — 接线 / 透传

| # | 问题 | 修复 |
|---|------|------|
| B3 | Sync 丢弃 `quality`（FE 类型已声明） | `WorkbenchSyncResponse.quality` + 透传 |
| B4 | KG ingest 失败 → `kg_written=null`，UI 不刷新 strip | 失败返回 `-1` 或 `kg_error`；`typeof number` 仍刷新；tip 区分失败 |
| W2 | Recommend `relation_insight` 默认关且 FE 不传 | opts 默认开（或显式 true）+ 榜卡轻量洞察条 |
| W3 | `archiveMaterial` / `materialsImportTemplateUrl` 死客户端 | MaterialsPanel 归档 + 下载模板 |
| W4 | `promote-from-requirement` 无 FE | api + 材料面板「从需求提升」 |

---

## 3. P2 — 本 PR 尽量合

| # | 问题 | 修复 |
|---|------|------|
| W5 | `POST .../exports/upload` 无客户端 | `uploadProjectExport` + ArtifactDrawer 上传 |
| B5 | dossier notify `except: pass` | `logger.warning`；跳过原因可观测（不改默认 auto_patch=off） |
| C1 | 死客户端 `getWikiPage` 等 | 本轮不删，避免无关 diff；记债 |

---

## 4. 明确不做（本轮）

- 关 `datalab_required` / 改产品 ELN 默认（需产品决策 + Docker）
- Dossier 全量 section 编辑器（patchWikiDossier）— 工作量大，另开
- Golden questions 列表 UI、formulation skill detail
- 删除死客户端 / OpenAPI 隐藏路由大扫除

---

## 5. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_kg_provenance.py \
  tests/test_grayscale_kg_maintrack_gate.py \
  tests/test_experiments_search_ids.py  # 新建
cd frontend && npx vitest run src/components/kgMeasuredObservability.test.tsx \
  src/store/slices/searchSlice*.test.* # 若有 claims 相关
```

- [x] 本 PR 合入前自测
- [x] 计划文档 `2026-09-24-wiring-closure-bugfix.md`
- [x] pytest search ids + recent_bias + gate (9 passed)
- [x] vitest tip / strip / ArtifactDrawer；`tsc --noEmit`
- [x] Chat claims UI 接线
- [x] Materials archive / template / promote-from-requirement
- [x] Shelf binary upload
- [x] relation_insight 默认可开 + 榜单洞察条
