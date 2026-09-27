# Post–E-Lit OpenScience Top-5（下一波候选）

> 状态：**仅评估，未开工**（2026-09-27）  
> 源：`/tmp/openscience`（SynSci）· `/tmp/aipoch-open-science`（AIPOCH）  
> 对照：FormuMind 已合入 Wave A–D2（#164–#168）+ **E-Lit**（#169，ChemRxiv，不含 arXiv）

## 产品边界

- **要**：配方 / 涂料 R&D 闭环 + Evidence / 报告钢印；文献库已可用最小闭环
- **仍不做**（除非另开）：Marketplace · Notebook/SSH/Slurm/HPC · 完整 ACP 多 backend · 全量 Specialist · 全量 pdf-structure · `.science` 主路径 · **arXiv 通道**（预印本继续 ChemRxiv）

## 已借（勿重复）

| Wave | 项 |
|------|----|
| A | 导出 Preflight · Evidence Reviewer · MCP 勾选/会话审批 |
| B | PaperQA · Manifest/Freeze · 关键词筛选 |
| C | OpenAlex 引用扩展 · `notice_kind` · `evidence_provenance` · `citations` · ChEBI |
| D | `sources` 审计 · Locator 诚实度 · 冻结 OA 补全文 |
| D2 | 轻量 RO-Crate · `peer-review` skill |
| **E-Lit** | Hub 文献库 Tab · tags/notes/collections · DOI/**ChemRxiv** 导入 · 查重合并 · BibTeX/RIS |

**现状缺口一句话**：钢印栈与文献库已厚，缺（1）领域回归门禁、（2）准则化 LLM 筛文、（3）专利/FTO 技能纪律、（4）Agent 可操作的 Library 工具面。

## 评分口径

- **必要性**（1–5）：对钢印诚实度 / 配方文献·专利工作流 / 可交换性的直接增益  
- **可行性**（1–5）：复用 FM Manifest/Library/KB/Hub/Skills，不引 Electron/Prisma/HPC，可 1–2 周切片  
- **排序** = 必要性优先，同分看可行性与主路径贴合度

---

## Top-5 推荐（Wave F 候选）

### 1. 涂料 / 配方 Evidence Golden Bench（质量闸）

| | |
|--|--|
| **源** | SynSci `evals/science-harness` 纪律（任务冻结、grader、soft→hard）× FM 已有 `golden_eval` / `golden_retrieval` |
| **必要性** | **5** — A–E 钢印与 Library 已厚，无领域回归则「看起来诚实」无法守门 |
| **可行性** | **3** — 出题成本高；跑通 pytest/nightly 可行 |
| **FM 落点** | `backend/evals/coatings_evidence/` + CI soft gate；指标：citation 绑定率、`sources_audit` 矛盾检出、locator 覆盖、冻结语料 DOI 命中 |
| **切片** | F1a 20 题种子（工艺窗/DOI/撤稿/无据/ChemRxiv 预印本）+ 离线 fixture → F1b 扩到 50 → F1c CI soft→hard |
| **明确不做** | 照搬 Harbor / ResearchClaw / DrugDiscoveryBench 全科学基准 |

### 2. Smart Screening LLM（系统综述路径）

| | |
|--|--|
| **源** | AIPOCH `smart-collections.ts`（~1866）· `smart-evidence.ts`（~80）· `smart-run-progress.ts` |
| **必要性** | **4** — Wave B 仅关键词；E-Lit 库有编目后，涂料系统综述/竞品扫文献需要纳入·排除准则 + 批量裁决 |
| **可行性** | **3** — 成本与进度状态机不可忽视；可先「单次批量 + 可暂停」 |
| **FM 落点** | 扩展 `literature_screening.py`；旗标 `literature_screening_llm_enabled`（默认关）；UI 在 Hub 文献库 / FreezeStrip |
| **切片** | F2a 准则 JSON + 单篇 LLM 裁决（写回 screening + reason）→ F2b 批量 job + 进度 → F2c（可选）match 自动 freeze |
| **明确不做** | 不停跑的 automatic updates 收藏夹；替代系统综述协议；整包 smart-collections 进度全家桶 |

### 3. Patent Mining / FTO 纪律 Skill

| | |
|--|--|
| **源** | SynSci `backend/cli/skills/research/patent-mining`（家族/kind code/CPC/查询日记录） |
| **必要性** | **4** — 配方平台刚需自由实施空间与竞品专利；FM 已有 USPTO/EPO/SureChEMBL 检索，缺「可辩护计数与 claim 纪律」技能层 |
| **可行性** | **4** — 以 Chat Skill 为主（少代码），约束模型：家族去重、日期字段、查询快照、勿把说明书当权利要求 |
| **FM 落点** | `chat_skills/patent-mining/SKILL.md` + 可选轻量 `patent_query_log` 写入 Manifest/事件；Hub/对话 `/` 选用 |
| **切片** | F3a Skill + 示例提示 → F3b 与现有 patent/SureChEMBL Evidence 字段对齐（family id 若已有则露出）→ F3c（可选）查询审计条 |
| **明确不做** | Lens/BigQuery 商用 bulk；完整 FTO 法律意见引擎 |

### 4. Literature Library Agent Tools（薄 MCP / Chat tools）

| | |
|--|--|
| **源** | AIPOCH `library-mcp-server.ts`（~828） |
| **必要性** | **4** — E-Lit API 已齐，但对话 Agent 不能稳定「导入 DOI / 查重 / 冻结」；钢印链路需要 Agent 可调用的台账操作 |
| **可行性** | **4** — 对现有 `/api/wiki/literature/*` 包一层只读+有界写工具（import-ids / list / freeze / merge 预览），不移植 Electron MCP 运行时 |
| **FM 落点** | `chat_chem_tools` 风格或 builtin connector `literature_library`；旗标 `literature_library_tools_enabled`（默认关） |
| **切片** | F4a list/search/get + import-ids → F4b freeze/screen（需确认）→ F4c merge 仅 preview |
| **明确不做** | 桌面 Library MCP 全协议；附件/PDF blob 工具 |

### 5. Library 元数据补全 + OA 联动（Metadata Enrich）

| | |
|--|--|
| **源** | AIPOCH `metadata-enricher.ts`（~200）· 现有 FM `literature_oa_enrich` / OpenAlex |
| **必要性** | **3** — import-ids fail-open 常留下裸 DOI；编目/BibTeX/钢印都受益于 authors/year/ChemRxiv id 回填 |
| **可行性** | **5** — 纯后端批处理 + Hub「补全元数据」按钮；复用 OpenAlex/ChemRxiv public API |
| **FM 落点** | `POST /api/wiki/literature/enrich-metadata`；Library 顶栏按钮；与 OA 补全文分步（先元数据后全文） |
| **切片** | F5a 缺 authors/year 的条目批补 → F5b ChemRxiv id 从 DOI/URL 回填 → F5c 与 enrich-oa 顺序提示 |
| **明确不做** | Crossref 全字段 typeFields；付费 Publisher TDM |

---

## 对比总表

| # | 项 | 主源 | 必要性 | 可行性 | 建议旗标 / 入口 |
|---|----|------|--------|--------|-----------------|
| 1 | 涂料 Evidence Golden Bench | SynSci harness 纪律 × FM golden | 5 | 3 | CI / nightly |
| 2 | Smart Screening LLM | AIPOCH smart-collections | 4 | 3 | `literature_screening_llm_enabled`（默认关） |
| 3 | Patent Mining Skill | SynSci patent-mining | 4 | 4 | Chat Skill `patent-mining` |
| 4 | Library Agent Tools | AIPOCH library-mcp | 4 | 4 | `literature_library_tools_enabled`（默认关） |
| 5 | Metadata Enrich | AIPOCH metadata-enricher | 3 | 5 | 随 Library；`enrich-metadata` |

## 建议打包策略

| 方案 | 内容 | 说明 |
|------|------|------|
| **F-Quality（推荐）** | **1** 优先 | 钢印回归，不挡产品功能 |
| **F-Lit+** | **2 → 5**（可并行 4） | 文献库从「台账」升级到「可筛 / 可补全 / 可被 Agent 用」 |
| **F-IP** | **3** | 专利纪律，可与 F-Quality 并行（几乎纯 Skill） |
| **F-Full** | 1∥3 → 2→5→4 | 质量+IP 先落，再加深 Library |

## 候补（未进前 5）

| 项 | 原因 |
|----|------|
| LaTeX bibliography / `latex-bundle` 轻量包 | 写作便利；method-writer 已够用，必要性中等（可行性高，可作 F6） |
| RO-Crate complete（附 PDF 字节） | D2 lite 已够交换；必要中等 |
| Library ↔ PaperQA 双向同步加深 | E-Lit 已同源 Manifest；边际 |
| PMID/PMCID 导入 | 偏生医；涂料主路径 ChemRxiv/DOI 已够（毒理场景另开） |
| SynSci `experimental-design` skill | FM 已有 DOE API/域模型；Skill 叠加边际 |
| SynSci `paper-lookup` 18 API 全家桶 | 与现检索栈重叠；维护面大 |
| AIPOCH figure-composer / paper-narrative | 绑 Notebook/Artifact 版本，栈错位 |
| Zotero 同步（SynSci pyzotero） | 协议/冲突成本高 |
| ChEMBL connector | PubChem/ChEBI/SureChEMBL 已覆盖 |
| 全量 pdf-structure / PDF 批注工作台 | UX/栈面过大 |

## 明确不借（重申）

Electron · ACP 多 backend · Notebook/SSH/Slurm · Skills Marketplace · 全量 Specialist · 整包 `pdf-structure` · `.science` 主路径 · **arXiv** · AIPOCH 生物结构预测 skills（AlphaFold 等）

## 请评估

1. 是否锁定 **F-Quality = 1** 与/或 **F-Lit+ = 2→5** / **F-IP = 3**？  
2. Top-5 内想先做哪几项（可点名子集）？  
3. Golden Bench 目标题量：20 种子 or 直接冲 50？  
4. 确认后回复「写详细方案再开工」或点名子集。
