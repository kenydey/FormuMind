# Post–Wave D2 OpenScience Top-5（含完整 Literature Library）

> 状态：**仅评估，未开工**（2026-09-27）  
> 源：`/tmp/openscience`（SynSci）· `/tmp/aipoch-open-science`（AIPOCH）  
> 对照：FormuMind `main` 已合入 Wave A–D2（#164–#168）

## 产品边界

- **要**：配方 R&D 闭环 + Evidence / 报告钢印；**本次评估显式纳入完整 Literature Library**
- **仍不做**（除非另开）：Marketplace · Notebook/SSH/Slurm/HPC · 完整 ACP 多 backend · 全量 Specialist 子代理 · 全量 pdf-structure 引擎（139+ 文件）· `.science` 主路径

## 已借（勿重复）

| Wave | 项 |
|------|----|
| A | 导出 Preflight · Evidence Reviewer 有界修正 · MCP 勾选/会话审批 |
| B | PaperQA · Manifest/Freeze · **轻量关键词**筛选 |
| C | OpenAlex 引用扩展 · `notice_kind` · `evidence_provenance` · `citations` · ChEBI |
| D | `sources` 审计 · Locator 诚实度 · 冻结 OA 补全文 |
| D2 | 轻量 RO-Crate · `peer-review` skill |

**现状缺口一句话**：Hub 仅有 `LiteratureFreezeStrip`（捕获/冻结/关键词筛/OA 补全文），**不是**可检索、可编目、可导入导出的文献库。

## AIPOCH Literature Library 体量（供可行性校准）

| 模块 | 约行数 | 含义 |
|------|--------|------|
| `catalog.ts` | ~3100 | 条目 / 集合 / 标签 / 检索 / 导入冲突 |
| `smart-collections.ts` | ~1900 | LLM 智能筛选（进度/暂停/重试） |
| `pdf-structure/*` | 139 文件 | 图表/版面（**整引擎不借**） |
| `duplicates` + `duplicate-metadata` | ~250 | 查重与合并计划 |
| `citation-exchange` / formatter / style-library | 中等 | BibTeX/RIS/CSL |
| `reference-resolver` + `metadata-enricher` | ~700 | 标识符解析与元数据补全 |
| `pdf-importer` + full-text-* | 已部分对齐 FM `fulltext_fetcher` | |

→ **「完整 Library」应按切片落地**，不可一次性移植 catalog.ts。

## 评分口径

- **必要性**（1–5）：对钢印语料质量 / 配方文献工作流 / 可交换性的直接增益  
- **可行性**（1–5）：复用 FM Manifest/KB/Hub、不引 Electron/Prisma、可 1–2 周切片  
- **排序** = 必要性优先，同分看可行性与与主路径贴合度

---

## Top-5 推荐（Wave E 候选）

### 1. Literature Library Hub 面板（完整库 · 分阶段）

| | |
|--|--|
| **源** | AIPOCH `literature/catalog.ts` + UI 能力地图（ROADMAP「Literature library」行） |
| **必要性** | **5** — FreezeStrip 无法承担「编目 / 检索 / 集合 / 笔记」；钢印与 PaperQA 需要稳定语料台账 |
| **可行性** | **MVP 4 / 全量 2** — 在 `literature_manifest` + `source_store` 上长出 Hub「文献库」Tab，而非移植 Prisma catalog |
| **FM 落点** | Knowledge Hub 新 Tab；API 扩展 `/api/wiki/literature/*`（list/patch/tags/notes/collections） |
| **切片** | **E1a** 列表+详情+编辑元数据+标签/笔记 · **E1b** 集合（manual collections）· **E1c** 与 Freeze/OA/PaperQA 双向同步 |
| **完整库定义（验收）** | 条目 CRUD · 标签/笔记 · 集合 · 标识符导入 · 查重 · BibTeX/RIS · 预览链接；**不含** PDF 批注工作台、smart-collections 全量 |
| **明确不做（本项）** | 全量 pdf-structure、桌面 PDF 阅读器、Zotero 同步协议全兼容 |

### 2. 标识符批量导入 + 查重合并

| | |
|--|--|
| **源** | AIPOCH `reference-resolver.ts` · `duplicates.ts` · `duplicate-metadata.ts` · `metadata-enricher.ts` |
| **必要性** | **5** — Library 无查重则 DOI/标题重复污染冻结集与钢印 |
| **可行性** | **4** — DOI/arXiv/OpenAlex 解析可复用 `scholar_helpers`；查重启发式（DOI > 规范化标题）可纯 Python |
| **FM 落点** | `POST /api/wiki/literature/import-ids`；`GET .../duplicates`；`POST .../merge` |
| **切片** | E2a DOI/arXiv 粘贴导入 → E2b 重复组预览 → E2c 一键合并（保留 screening/freeze 态） |
| **明确不做** | 模糊作者聚类的重型 ML |

### 3. BibTeX / RIS 导入 · 导出

| | |
|--|--|
| **源** | AIPOCH `citation-exchange.ts` · `citation-formatter.ts` · `citation-style-library` |
| **必要性** | **4** — 与外部写作/Zotero/期刊提交流程互通；RO-Crate 旁的「文献交换」刚需 |
| **可行性** | **5** — 冻结集/库条目 → BibTeX/RIS 字符串；导入解析可用现成轻量库或手写子集 |
| **FM 落点** | `GET/POST /api/wiki/literature/export.{bib,ris}` · `import`；Hub 按钮 |
| **切片** | E3a 导出（frozen 或 library scope）→ E3b 导入（冲突走 E2 查重） |
| **明确不做** | 全 CSL 样式引擎、LaTeX 全文包（`latex-bundle` 后置） |

### 4. Smart Screening LLM 升级（系统综述路径）

| | |
|--|--|
| **源** | AIPOCH `smart-collections.ts` · `smart-evidence.ts`（纳入/排除准则 + 批量裁决 + 进度） |
| **必要性** | **4** — Wave B 仅关键词；涂料系统综述/竞品扫文献需要准则化 LLM triage |
| **可行性** | **3** — 成本与进度状态机不可忽视；可先做「单次批量 + 可暂停」，不做 automatic updates 全家桶 |
| **FM 落点** | 扩展 `literature_screening.py`；旗标 `literature_screening_llm_enabled`（默认关）；UI 在 FreezeStrip/Library |
| **切片** | E4a 准则 JSON + 单篇 LLM 裁决 → E4b 批量 job + 进度 → E4c（可选）auto-freeze match |
| **明确不做** | 替代系统综述协议；不停跑的「自动更新收藏夹」 |

### 5. 涂料 / 配方 Evidence Golden Bench（质量闸）

| | |
|--|--|
| **源** | SynSci `evals/science-harness` 纪律 + FM 已有 `golden_eval` 基建 |
| **必要性** | **4** — A–D2 钢印栈已厚，缺领域回归集则「看起来诚实」无法守门 |
| **可行性** | **3** — 需人工编 30–50 题（工艺窗/DOI/撤稿/无据）；跑通 pytest 门禁可行，出题成本高 |
| **FM 落点** | `backend/evals/coatings_evidence/` + CI job（可 nightly）；指标：citation 绑定率、sources_audit 矛盾检出、locator 覆盖 |
| **切片** | E5a 20 题种子 + 离线 fixture → E5b 扩到 50 → E5c CI soft→hard gate |
| **明确不做** | 照搬 SynSci Harbor 全科学基准 |

---

## 对比总表

| # | 项 | 主源 | 必要性 | 可行性 | 建议旗标 / 入口 |
|---|----|------|--------|--------|-----------------|
| 1 | Literature Library Hub（分阶段完整库） | AIPOCH catalog | 5 | 4→2 | `literature_library_enabled`（默认关→浸泡开） |
| 2 | 标识符导入 + 查重合并 | AIPOCH duplicates/resolver | 5 | 4 | 随 Library；或先独立 API |
| 3 | BibTeX/RIS I/O | AIPOCH citation-exchange | 4 | 5 | 随 Library |
| 4 | Smart Screening LLM | AIPOCH smart-collections | 4 | 3 | `literature_screening_llm_enabled`（默认关） |
| 5 | 涂料 Evidence Golden Bench | SynSci evals 纪律 × FM golden | 4 | 3 | CI / nightly |

## 建议打包策略

| 方案 | 内容 | 说明 |
|------|------|------|
| **E-Lit（推荐）** | **1 → 2 → 3** | 先建成「完整 Library」可用最小闭环 |
| **E-Lit+Smart** | 1–3 后接 **4** | 系统综述路径 |
| **E-Quality** | **5** 与 E-Lit 并行 | 不挡 Library，专波质量 |

## 候补（未进前 5）

| 项 | 原因 |
|----|------|
| 全量 pdf-structure / PDF 批注工作台 | UX/栈面过大；Locator 已覆盖钢印刚需 |
| RO-Crate complete（附 PDF 字节） | D2 lite 已够交换；必要中等 |
| ChEMBL connector | PubChem/ChEBI/SureChEMBL 已覆盖主路径 |
| hypotheses / experimental-design skill | FM 已有 DOE；边际 |
| reproduce skill（计算复现） | 偏 notebook/HPC，边界外 |
| ACP Reviewer / Specialist | 栈错位 |

## 明确不借（重申）

Electron · ACP 多 backend · Notebook/SSH/Slurm · Skills Marketplace · 全量 Specialist · 整包 `pdf-structure` · `.science` 作为主交换路径

## 请评估

1. 是否锁定 **E-Lit = 1→2→3** 作为下一波？  
2. **4（LLM 筛选）** / **5（Golden Bench）** 是否并入或另波？  
3. Library 默认旗标：默认关浸泡，还是默认开？  
4. 确认后回复「写详细方案再开工」或点名子集。
