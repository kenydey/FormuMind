# 卷宗 Report 灰度冒烟（Hub 生成→导出 MD）

> 状态：**已实现**（2026-09-18）  
> 前置：#117 产物抽屉接入 Hub Reports；P5 / P5.1 生成与 `POST /api/wiki/dossier/report/export` 已落地  
> 缺口：灰度门只断言「生成」未锁「导出 MD」；Hub UI smoke 未点导出；活栈 MD 导出是 soft 检查  
> 约束：**不重写** report/export 引擎；不默认开 LLM；旗标灰度；复用现有 Hub / smoke 栈

## 0. 结论摘要

| 本 MVP 做 | 不做 |
|-----------|------|
| 灰度 TestClient：briefing 生成 → `format=md` 导出硬断言 | DOCX/PDF/PPTX 硬依赖（仍 soft） |
| Hub Playwright：点「导出 MD」并捕获下载 | 全库 scrub / Milvus |
| 活栈 API smoke：MD 导出改为必过 | 改默认旗标 / 默认 LLM |
| 组件单测：`exportWikiReport({ format: "md" })` | 重写 `report.py` |

## 1. 决策锁定

1. **MD 永远可用**（`export_capabilities()["md"] is True`）→ 灰度与活栈均 **硬失败**，不再 soft-warn  
2. **路径**：活动项目 → Hub Reports → briefing 生成 → 导出 MD（与 #117 入口一致）  
3. **旗标**：与既有灰度一致开 dossier/report；`use_llm=false`  
4. **信任边界**：导出响应头仍带 `draft_not_claims`；不把报告当 Claims  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-18-wiki-report-grayscale-smoke.md` | 本方案 |
| `backend/tests/test_wiki_grayscale_gate.py` | 追加 generate→export MD |
| `scripts/hub_dossier_handtest_smoke.py` | MD 导出必过 + disclaimer 头 |
| `frontend/scripts/hub_dossier_smoke.mjs` | 点击导出 MD、校验下载 |
| `HubReportsPlaceholderPane.test.tsx` | 导出 MD 调用断言 |
| `docs/plans/2026-09-14-wiki-hub-dossier-handtest.md` | R4/R5 对齐本冒烟 |

## 3. 验证

```bash
cd backend && python -m pytest -q tests/test_wiki_grayscale_gate.py
cd frontend && npm test -- --run src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
# 活栈（可选）：
python3 scripts/hub_dossier_handtest_smoke.py
node frontend/scripts/hub_dossier_smoke.mjs
```

- 灰度：`POST /api/wiki/dossier/report` ok + `/export` format=md → 200、正文含标题、`X-FormuMind-Disclaimer=draft_not_claims`  
- Hub：生成后点「导出 MD」触发下载（文件名含 `.md`）  
- 回归：既有 Claims/DOE 隔离用例仍绿  
