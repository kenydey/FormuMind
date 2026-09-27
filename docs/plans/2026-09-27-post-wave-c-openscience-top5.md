# Post–Wave C OpenScience Top-5 — **已锁定**

> 状态：**主波已合入 · 次波已实施**（2026-09-27）  
> 施工方案：[`2026-09-27-wave-d-sources-locator-oa.md`](./2026-09-27-wave-d-sources-locator-oa.md)  
> 源：`/tmp/openscience`（SynSci）· `/tmp/aipoch-open-science`（AIPOCH）

## 锁定策略（用户确认）

| 层级 | ID | 项 |
|------|-----|-----|
| **主波（优先）** | D1 → D2 → D3 | Sources 审计 · Locator 诚实度 · 冻结 OA 补全 |
| **次波（可并行）** | D4 ∥ D5 | 轻量 RO-Crate · Peer-review skill |

## 产品边界（不变）

- **要**：配方 R&D 闭环 + Evidence / 报告 **钢印诚实度**
- **不要**：Marketplace · Notebook/SSH/Slurm/HPC · 完整 ACP · 完整 Literature Library · 全量 Specialist

## 已借（A/B/C）

| Wave | 项 |
|------|----|
| A | 导出 Preflight · Evidence Reviewer 有界修正 · MCP 勾选/会话审批 |
| B | PaperQA 解耦 · 文献 Manifest/冻结 · 轻量关键词筛选 |
| C | OpenAlex 引用扩展 · `notice_kind` · `evidence_provenance` · `citations` skill · ChEBI |

## Top-5 摘要

| # | 项 | 必要性 | 可行性 | 旗标（默认） |
|---|----|--------|--------|--------------|
| 1 | Sources 审计 skill + claim 表 | 5 | 5 | `sources_audit_enabled` (true) |
| 2 | Locator 诚实度（页码/段落） | 5 | 4 | `citation_locator_preflight` (`warning`) |
| 3 | 冻结语料 OA 批量补全 | 4 | 5 | `literature_oa_enrich_enabled` (true) |
| 4 | 轻量 RO-Crate 导出 | 4 | 3 | `ro_crate_export_enabled` (false) |
| 5 | Peer-review skill（卷宗） | 4 | 4 | skill user-controlled |

## 下一步

主波 D1–D3 已合入 (#167)；次波 D4∥D5 见 `cursor/wave-d2-rocrate-peerreview`。
