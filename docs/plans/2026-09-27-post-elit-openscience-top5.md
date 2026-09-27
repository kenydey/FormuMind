# Post–E-Lit OpenScience Top-5（下一波候选）

> 状态：**仅评估，未开工**（2026-09-27；二次复核：克隆深挖 + FM `chemtools` GHS 仅 H200–H208）  
> 源：`/tmp/openscience`（SynSci）· `/tmp/aipoch-open-science`（AIPOCH）  
> 对照：FormuMind 已合入 Wave A–D2（#164–#168）+ **E-Lit**（#169，ChemRxiv，不含 arXiv）

## 决策快表（供勾选）

| 优先级 | 项 | 必要×可行 | 推荐包 | 一句话 |
|--------|----|-----------|--------|--------|
| **P0** | 涂料 Evidence Golden Bench | 5×3=15 | **F-Quality** | 钢印回归门禁 |
| **P0** | Patent Mining Skill | 4×4=16 | **F-IP** | 专利纪律，几乎纯 Skill，可与 P0 质量并行 |
| **P1** | Library Agent Tools | 4×4=16 | **F-Lit+** | Agent 操台账，贴钢印链路 |
| **P1** | Smart Screening LLM | 4×3=12 | **F-Lit+** | 准则化筛文（成本敏感） |
| **P1** | Metadata Enrich | 3×5=15 | **F-Lit+** | 裸 DOI 回填，最快见效 |
| **插队·Chem** | PubChem **完整 GHS**（非仅爆炸物） | 4×4=16 | **F-Chem-A** | FM 现仅 H200–H208；SDS/VOC 危害缺口 |
| **插队·Chem** | SMILES 校验门 + fetch-outcome | 4×5=20 | **F-Chem** | 改动小、诚实度高 |
| **插队·Lab** | Analytical method validation skill | 4×4=16 | **F-Lab** | 涂料 QC / ICH 纪律 |

> 同分时：先做「纯 Skill / 小改动诚实度」，再做「出题/LLM 成本」项。

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

### SynSci 向补充打包（未挤进 Top-5，可插队）

来自 SynSci 深挖：约 25 项边界内可借；下列三桶与主表互补，**不改变 Top-5 排序**，但若你更偏配方 QC / 化学诚实度，可点名并入 Wave F。

| 桶 | 项（必要性约分） | 源 | 说明 |
|----|------------------|----|------|
| **F-Chem** | SMILES 校验门（**4**/可行 **5**）· Connector fetch-outcome 空结果≠故障（**4**/可行 **5**）· ChEMBL（**3**/可行 **4**） | `skills/chemistry/smiles-validation` · `connectors/fetch-outcome.ts` · `chembl.ts` | 结构/连接器诚实度；ChEMBL 对功能助剂有用，主路径已有 PubChem/ChEBI/SureChEMBL |
| **F-Lab** | Analytical method validation skill（**4**/可行 **4**）· `compare` 先冻结判据（**3**/可行 **5**）· statistical-conventions（**3**/可行 **5**） | `skills/chemistry/analytical-method-validation` · `research/compare` · `core/statistical-conventions` | 涂料 QC / 盐雾等试验报告纪律；与 `method-writer`/DOE 衔接 |
| **F-Ops** | Export `EXPORTED`/`PARTIAL`/`NOT EXPORTED` 终态（**3**/可行 **5**）· skill-load SHA 审计（**3**/可行 **4**）· install Layer-2 注入拒绝（**3**/可行 **4**）· provenance reason 细分类（**3**/可行 **4**） | `skills/research/export` · skill-runtime / install review · `provenance/envelope.ts` | 钢印包装与 Skills 运营抛光，贴 D2 RO-Crate |

### SynSci 残差（Top-5 / 上表未覆盖 · 二次扫描）

排除已列 Top-5 与 F-Chem/Lab/Ops 后，仍值得单独记账的 8 项（**不改变 Top-5**）：

| # | 项 | 必要 | 可行 | 源 | 说明 |
|---|----|------|------|----|------|
| 1 | Cheminformatics definitions | 4 | 5 | `skills/chemistry/cheminformatics-definitions` | 钉死 HBD/HBA/TPSA/InChI/canonical SMILES 口径（≠ SMILES 语法门） |
| 2 | Statistical power / MDE | 4 | 4 | `skills/research/statistical-power` | DOE/QC 先验样本量；≠ statistical-conventions |
| 3 | Uncertainty & units | 4 | 4 | `skills/physics/uncertainty-and-units` | 膜厚/VOC/盐雾等 ± 与单位诚实 |
| 4 | Harness composition manifests | 4 | 3 | `docs/notes/harness-manifests.md` | prompt/tool schema 指纹进钢印（≠ Golden 出题） |
| 5 | Research-contract preregistration | 4 | 3 | `session/research.ts` · research-workflows | 试验前冻结分析计划 artifact |
| 6 | Analysis-report skill | 3 | 5 | `skills/core/analysis-report` | 条款→步骤图 + 决策日志；贴 method-writer |
| 7 | Provenance review lifecycle | 3 | 4 | `science/provenance/review.ts` | open→addressed→confirmed；修正不自关 |
| 8 | Acceptance-checks skill | 3 | 4 | `skills/core/acceptance-checks` | 把工艺窗写成可运行出口检查 |

### AIPOCH 向补充打包（未挤进 Top-5，可插队）

来自 AIPOCH 深挖：Top-5 中 2/4/5 已覆盖其最高优先；下列为同栈延伸，**不改变 Top-5 排序**。

| 桶 | 项（必要性约分） | 源 | 说明 |
|----|------------------|----|------|
| **F-Lit-Deep** | 批量 Library jobs / journal（**3**/可行 **3**）· 多源 full-text finder（**3**/可行 **4**）· 本地 PDF passage index（**3**/可行 **3**）· Agent PDF inbox（**3**/可行 **3**） | `batch-jobs.ts` · `full-text-finder.ts` · `full-text-index.ts` · `agent-pdf-acquisition.ts` | 50–200 篇涂料战役的补全/全文；passage index 可加强 locator，**不**引 pdf-structure |
| **F-Cite-Out** | CSL 样式 / `format_references`（**3**/可行 **4**）· LaTeX+bib 轻量包（**3**/可行 **5**）· DOCX `{{cite}}`（**2**/可行 **3**） | `citation-formatter.ts` · `latex-bundle.ts` · `citation-document.ts` | 期刊向书目；贴 method-writer / RO-Crate |
| **F-Chem-A** | PubChem **完整 GHS** + **similarity**（**4**/可行 **4**）· molecule preview（**3**/可行 **3**）· Rhea（**2**/可行 **3**）· ZINC 可购（**2**/可行 **3**） | `connectors/descriptors/chemistry.ts` · `molecule/*` · `zinc.ts` | 配方日用：危害分类 / 取代基搜索；优于再接一层 ChEMBL |
| **F-Stamp-UX** | `citations`/`sources` always-on activationPolicy（**3**/可行 **5**）· RO-Crate complete 附 PDF 字节（**3**/可行 **3**）· prepared-literature sidecar（**3**/可行 **4**） | `activation-policy.ts` · `ro-crate-export.ts` · `prepared-literature-sidecar.ts` | 钢印会话强制引用纪律；包内绑死文献 digest |

## 候补（未进前 5）

| 项 | 原因 |
|----|------|
| **SMILES 校验门**（SynSci） | 必要性高、可行极高；未进 Top-5 因偏「化学工具」而非钢印/文献主轴——**建议作 F-Chem 首选插队** |
| **Fetch-outcome 诚实**（空≠故障） | 钢印诚实度增益大、改动小；可并进任意连接器波 |
| **Analytical method validation** skill | 涂料 QC 强相关；与 Golden Bench / method-writer 互补 |
| **Cheminformatics definitions** / **statistical-power** / **uncertainty-and-units** | SynSci 残差；定义·样本量·单位诚实，Skill 为主可快插 |
| Harness composition manifests / research-contract preregistration | 钢印指纹与试验前契约；可贴 F-Quality |
| Analysis-report / acceptance-checks / review lifecycle | 报告与复核纪律抛光 |
| Export PARTIAL 终态 / skill SHA / provenance reason 细分类 | D2 抛光；必要性中等 |
| **PubChem 完整 GHS + similarity**（AIPOCH） | 配方日用强；建议作 **F-Chem-A** 首选（比再接 ChEMBL 更贴主路径） |
| ChEMBL connector | PubChem/ChEBI/SureChEMBL 已覆盖主路径；助剂 bioactivity 场景可开 |
| 批量 Library jobs / 多源 OA finder / PDF passage index | 文献战役吞吐；可并进 F-Lit+ 后续切片 |
| LaTeX / CSL bibliography（AIPOCH） | 写作便利；method-writer 已够用（可作 F6） |
| RO-Crate complete（附 PDF 字节）· literature sidecar | D2 lite 已够交换；伙伴交付时可开 |
| `citations`/`sources` always-on | 一行策略开关；可并进任意钢印波 |
| Library ↔ PaperQA 双向同步加深 | E-Lit 已同源 Manifest；边际 |
| PMID/PMCID 导入 | 偏生医；涂料主路径 ChemRxiv/DOI 已够 |
| SynSci `experimental-design` / `hypotheses` / `reproduce` | FM 已有 DOE；reproduce 偏 notebook/HPC |
| SynSci `paper-lookup` 18 API 全家桶 | 与现检索栈重叠；维护面大 |
| SynSci literature-review 预算轮次加强 | FM 已有 AIPOCH 改编版；可后置加「exclusion ledger」 |
| AIPOCH figure-composer / paper-narrative | 绑 Notebook/Artifact，栈错位 |
| Zotero（SynSci pyzotero） | 协议/冲突成本高 |
| BindingDB / ZINC / docking / MD / BioNeMo | 药化/计算栈，边界外或弱相关 |
| 全量 pdf-structure / PDF 批注工作台 | UX/栈面过大 |
| Harbor / DrugDiscoveryBench 全科学基准 | 生物学榜单，不当产品门禁 |

## 明确不借（重申）

Electron · ACP 多 backend · Notebook/SSH/Slurm · Skills Marketplace · 全量 Specialist · 整包 `pdf-structure` · `.science` 主路径 · **arXiv** · AIPOCH 生物结构预测 skills（AlphaFold 等）· SynSci Harbor 生物 leaderboard 当产品 KPI

## 请评估

1. 是否锁定 **F-Quality = 1** 与/或 **F-Lit+ = 2→5** / **F-IP = 3**？  
2. 是否插队 **F-Chem**（SynSci SMILES + fetch-outcome）/ **F-Chem-A**（AIPOCH GHS+similarity）或 **F-Lab**（analytical-method-validation）？  
3. Top-5 内想先做哪几项（可点名子集）？  
4. Golden Bench 目标题量：20 种子 or 直接冲 50？  
5. 确认后回复「写详细方案再开工」或点名子集。
