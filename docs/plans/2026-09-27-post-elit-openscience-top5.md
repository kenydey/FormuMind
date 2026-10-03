# Post–E-Lit OpenScience Top-5（下一波候选）

> 状态：**仅评估，未开工**（2026-09-27；**产品边界已校正为全化学品**；二次复核 + **第三次全量目录**：克隆深挖 + FM `chemtools` GHS 仅 H200–H208）  
> 源：`/tmp/openscience`（SynSci）· `/tmp/aipoch-open-science`（AIPOCH）  
> 对照：FormuMind 已合入 Wave A–D2（#164–#168）+ **E-Lit**（#169，ChemRxiv，不含 arXiv）

## 决策快表（供勾选）

| 优先级 | 项 | 必要×可行 | 推荐包 | 一句话 |
|--------|----|-----------|--------|--------|
| **P0** | 化学品 Evidence Golden Bench | 5×3=15 | **F-Quality** | 钢印回归门禁（全 ProductDomain） |
| **P0** | Patent Mining Skill | 4×4=16 | **F-IP** | 专利纪律，几乎纯 Skill，可与 P0 质量并行 |
| **P1** | Library Agent Tools | 4×4=16 | **F-Lit+** | Agent 操台账，贴钢印链路 |
| **P1** | Smart Screening LLM | 4×3=12 | **F-Lit+** | 准则化筛文（成本敏感） |
| **P1** | Metadata Enrich | 3×5=15 | **F-Lit+** | 裸 DOI 回填，最快见效 |
| **插队·Chem** | PubChem **完整 GHS**（非仅爆炸物） | 4×4=16 | **F-Chem-A** | FM 现仅 H200–H208；SDS/危害缺口 |
| **插队·Chem** | SMILES 校验门 + fetch-outcome | 4×5=20 | **F-Chem** | 改动小、诚实度高 |
| **插队·Lab** | Analytical method validation skill | 4×4=16 | **F-Lab** | 化学品 QC / ICH（分析方法确认） |

> 同分时：先做「纯 Skill / 小改动诚实度」，再做「出题/LLM 成本」项。

## 产品边界

- **要**：**化学品配方 R&D** 闭环（覆盖全部 [`ProductDomain`](../../backend/app/domain/schemas.py)；涂料 / 脱脂 / 表面处理等为子集）+ Evidence / 报告钢印；文献库已可用最小闭环
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

### 1. 化学品 / 配方 Evidence Golden Bench（质量闸）

| | |
|--|--|
| **源** | SynSci `evals/science-harness` 纪律（任务冻结、grader、soft→hard）× FM 已有 `golden_eval` / `golden_retrieval` |
| **必要性** | **5** — A–E 钢印与 Library 已厚，无领域回归则「看起来诚实」无法守门 |
| **可行性** | **3** — 出题成本高；跑通 pytest/nightly 可行 |
| **FM 落点** | `backend/evals/chem_evidence/` + CI soft gate；指标：citation 绑定率、`sources_audit` 矛盾检出、locator 覆盖、冻结语料 DOI 命中 |
| **切片** | F1a 20 题种子（按 `ProductDomain` 分桶：工艺窗 / DOI / 撤稿 / 无据 / ChemRxiv；可含但不限于防腐蚀涂料场景）+ 离线 fixture → F1b 扩到 50 → F1c CI soft→hard。**套件命名与验收不得写成 coatings-only** |
| **明确不做** | 照搬 Harbor / ResearchClaw / DrugDiscoveryBench 全科学基准；涂料独占题集 |

### 2. Smart Screening LLM（系统综述路径）

| | |
|--|--|
| **源** | AIPOCH `smart-collections.ts`（~1866）· `smart-evidence.ts`（~80）· `smart-run-progress.ts` |
| **必要性** | **4** — Wave B 仅关键词；E-Lit 库有编目后，化学品 / 配方系统综述与竞品扫文献需要纳入·排除准则 + 批量裁决 |
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
| 1 | 化学品 Evidence Golden Bench | SynSci harness 纪律 × FM golden | 5 | 3 | CI / nightly · `evals/chem_evidence/` |
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
| **F-Lab** | Analytical method validation skill（**4**/可行 **4**）· `compare` 先冻结判据（**3**/可行 **5**）· statistical-conventions（**3**/可行 **5**） | `skills/chemistry/analytical-method-validation` · `research/compare` · `core/statistical-conventions` | **化学品 QC / ICH（分析方法确认）**；配方表征与分析方法纪律（可含但不限于 HPLC/GC/ICP、膜厚或盐雾等场景）；与 `method-writer`/DOE 衔接 |
| **F-Ops** | Export `EXPORTED`/`PARTIAL`/`NOT EXPORTED` 终态（**3**/可行 **5**）· skill-load SHA 审计（**3**/可行 **4**）· install Layer-2 注入拒绝（**3**/可行 **4**）· provenance reason 细分类（**3**/可行 **4**） | `skills/research/export` · skill-runtime / install review · `provenance/envelope.ts` | 钢印包装与 Skills 运营抛光，贴 D2 RO-Crate |

### SynSci 残差（Top-5 / 上表未覆盖 · 二次扫描）

排除已列 Top-5 与 F-Chem/Lab/Ops 后，仍值得单独记账的 8 项（**不改变 Top-5**）：

| # | 项 | 必要 | 可行 | 源 | 说明 |
|---|----|------|------|----|------|
| 1 | Cheminformatics definitions | 4 | 5 | `skills/chemistry/cheminformatics-definitions` | 钉死 HBD/HBA/TPSA/InChI/canonical SMILES 口径（≠ SMILES 语法门） |
| 2 | Statistical power / MDE | 4 | 4 | `skills/research/statistical-power` | DOE/QC 先验样本量；≠ statistical-conventions |
| 3 | Uncertainty & units | 4 | 4 | `skills/physics/uncertainty-and-units` | 工艺与表征量（如浓度、膜厚、VOC、耐蚀指标等）± 与单位诚实 |
| 4 | Harness composition manifests | 4 | 3 | `docs/notes/harness-manifests.md` | prompt/tool schema 指纹进钢印（≠ Golden 出题） |
| 5 | Research-contract preregistration | 4 | 3 | `session/research.ts` · research-workflows | 试验前冻结分析计划 artifact |
| 6 | Analysis-report skill | 3 | 5 | `skills/core/analysis-report` | 条款→步骤图 + 决策日志；贴 method-writer |
| 7 | Provenance review lifecycle | 3 | 4 | `science/provenance/review.ts` | open→addressed→confirmed；修正不自关 |
| 8 | Acceptance-checks skill | 3 | 4 | `skills/core/acceptance-checks` | 把工艺窗写成可运行出口检查 |

### AIPOCH 向补充打包（未挤进 Top-5，可插队）

来自 AIPOCH 深挖：Top-5 中 2/4/5 已覆盖其最高优先；下列为同栈延伸，**不改变 Top-5 排序**。

| 桶 | 项（必要性约分） | 源 | 说明 |
|----|------------------|----|------|
| **F-Lit-Deep** | 批量 Library jobs / journal（**3**/可行 **3**）· 多源 full-text finder（**3**/可行 **4**）· 本地 PDF passage index（**3**/可行 **3**）· Agent PDF inbox（**3**/可行 **3**） | `batch-jobs.ts` · `full-text-finder.ts` · `full-text-index.ts` · `agent-pdf-acquisition.ts` | 50–200 篇**配方文献战役**的补全/全文；passage index 可加强 locator，**不**引 pdf-structure |
| **F-Cite-Out** | CSL 样式 / `format_references`（**3**/可行 **4**）· LaTeX+bib 轻量包（**3**/可行 **5**）· DOCX `{{cite}}`（**2**/可行 **3**） | `citation-formatter.ts` · `latex-bundle.ts` · `citation-document.ts` | 期刊向书目；贴 method-writer / RO-Crate |
| **F-Chem-A** | PubChem **完整 GHS** + **similarity**（**4**/可行 **4**）· molecule preview（**3**/可行 **3**）· Rhea（**2**/可行 **3**）· ZINC 可购（**2**/可行 **3**） | `connectors/descriptors/chemistry.ts` · `molecule/*` · `zinc.ts` | 配方日用：危害分类 / 取代基搜索；优于再接一层 ChEMBL |
| **F-Stamp-UX** | `citations`/`sources` always-on activationPolicy（**3**/可行 **5**）· RO-Crate complete 附 PDF 字节（**3**/可行 **3**）· prepared-literature sidecar（**3**/可行 **4**） | `activation-policy.ts` · `ro-crate-export.ts` · `prepared-literature-sidecar.ts` | 钢印会话强制引用纪律；包内绑死文献 digest |

### AIPOCH 残差（Top-5 / 上表未覆盖 · 二次扫描）

排除已列 Top-5 与 F-Lit-Deep/Cite-Out/Chem-A/Stamp-UX 后，仍值得单独记账的 8 项（**不改变 Top-5**）：

| # | 项 | 必要 | 可行 | 源 | 说明 |
|---|----|------|------|----|------|
| 1 | Reviewer rubric: trace-not-recompute | 4 | 5 | `reviewer/rubric.ts` | Wave A 有 reviewer；缺「artifact≻prose / 伪造引用失败」契约 |
| 2 | Checksum-bound PDF attachment authority | 4 | 4 | `attachment-authority.ts` · `session-pdf-source-resolver.ts` | 字节漂移则 lease 失效；≠ locator 芯片 |
| 3 | Artifact-bound literature corpus | 4 | 4 | `artifacts/literature-manifest.ts` · `shared/artifact-literature.ts` | 报告版本绑定检索范围 + citation locators；≠ project Manifest |
| 4 | Reproducibility receipts（table/image compare，无 replay） | 4 | 3 | `artifact-reproducibility*.ts` · `output-comparison.ts` | DOE/图表 matched·different·missing-evidence；不做 notebook 重放 |
| 5 | DataCite + Zenodo 研究数据连接器 | 3 | 5 | `literature-doi.ts` datacite_* · `zenodo.ts` | 数据集/软件 DOI；**化学品 / 配方研究数据寄存** |
| 6 | Stale-review on turn scope drift | 3 | 4 | `reviewer/stale-reviews.ts` | 证据范围变化时作废旧评审 |
| 7 | Citation fidelity（跨导出保 locator/occurrence） | 3 | 4 | `citation-fidelity.test.ts` · `citation-document.ts` | ≠ CSL 排版；防钢印导出丢语义 |
| 8 | Smart screening rule history | 3 | 4 | `smart-rule-history.ts` | 准则版本审计；可贴 Smart Screening |

## 候补（未进前 5）

| 项 | 原因 |
|----|------|
| **SMILES 校验门**（SynSci） | 必要性高、可行极高；未进 Top-5 因偏「化学工具」而非钢印/文献主轴——**建议作 F-Chem 首选插队** |
| **Fetch-outcome 诚实**（空≠故障） | 钢印诚实度增益大、改动小；可并进任意连接器波 |
| **Analytical method validation** skill | **化学品 QC / ICH（分析方法确认）** 强相关；与 Golden Bench / method-writer 互补 |
| **Cheminformatics definitions** / **statistical-power** / **uncertainty-and-units** | SynSci 残差；定义·样本量·单位诚实，Skill 为主可快插 |
| Harness composition manifests / research-contract preregistration | 钢印指纹与试验前契约；可贴 F-Quality |
| Analysis-report / acceptance-checks / review lifecycle | 报告与复核纪律抛光 |
| **Reviewer rubric / stale-review / attachment authority**（AIPOCH） | 钢印复核契约与 PDF 字节权威；可并进 F-Quality |
| Artifact-bound literature · reproducibility receipts · citation fidelity | 报告级语料绑定与导出保真；可贴 D2/STORM |
| DataCite / Zenodo | 研究数据 DOI；主路径边际 |
| Smart screening rule history | 并进 Smart Screening 切片 |
| Export PARTIAL 终态 / skill SHA / provenance reason 细分类 | D2 抛光；必要性中等 |
| **PubChem 完整 GHS + similarity**（AIPOCH） | 配方日用强；建议作 **F-Chem-A** 首选（比再接 ChEMBL 更贴主路径） |
| ChEMBL connector | PubChem/ChEBI/SureChEMBL 已覆盖主路径；助剂 bioactivity 场景可开 |
| 批量 Library jobs / 多源 OA finder / PDF passage index | 文献战役吞吐；可并进 F-Lit+ 后续切片 |
| LaTeX / CSL bibliography（AIPOCH） | 写作便利；method-writer 已够用（可作 F6） |
| RO-Crate complete（附 PDF 字节）· literature sidecar | D2 lite 已够交换；伙伴交付时可开 |
| `citations`/`sources` always-on | 一行策略开关；可并进任意钢印波 |
| Library ↔ PaperQA 双向同步加深 | E-Lit 已同源 Manifest；边际 |
| PMID/PMCID 导入 | 偏生医；化学品主路径 ChemRxiv/DOI 已够 |
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

---

## 全量评估目录（第三次深扫 · 按必要×可行排序）

> **口径不变**：必要性 / 可行性各 1–5；**产品 = 全化学品配方 R&D**（全部 `ProductDomain`）+ Evidence 钢印。  
> **范围**：边界内「值得升级 / 新开发」的全部候选（含已入 Top-5 / 插队桶者，标 **已入账**）；生物学 / Electron / Notebook·HPC / 药化 docking 全家桶见文末「边界外」。  
> **不改变**上文 Top-5 排序与打包名；本表供勾选扩面。  
> **FM 对照要点**：`chemtools` GHS **仅 H200–H208**；pair 相似度仅本地 RDKit；Library 有 Manifest/Freeze/关键词筛/OA/ChemRxiv，**无** LLM 准则筛、Agent tools、元数据批补；Skills 仅 citations / literature-review / method-writer / peer-review / sources；无 `evals/chem_evidence/`。

### Tier S — 得分 ≥ 16（优先评估）

| # | 项 | 源 | 必要 | 可行 | 分 | 落点 / 备注 |
|---|----|----|------|------|----|-------------|
| S1 | **Connector fetch-outcome**（空结果≠故障） | SynSci `connectors/fetch-outcome.ts` | 4 | 5 | **20** | **已入账 F-Chem**；改连接器返回契约，钢印诚实度 |
| S2 | **SMILES 校验门**（解析/价态/声称改动核验） | SynSci `skills/chemistry/smiles-validation` | 4 | 5 | **20** | **已入账 F-Chem**；LLM 生成结构防呆 |
| S3 | **Cheminformatics definitions**（HBD/HBA/TPSA/InChI/canonical 口径） | SynSci `skills/chemistry/cheminformatics-definitions` | 4 | 5 | **20** | **已入账残差**；≠ SMILES 语法门；Skill 为主 |
| S4 | **Reviewer rubric: trace-not-recompute** | AIPOCH `reviewer/rubric.ts` | 4 | 5 | **20** | **已入账残差**；artifact≻prose、伪造引用失败 |
| S5 | **Patent Mining / FTO 纪律 Skill** | SynSci `skills/research/patent-mining` | 4 | 4 | **16** | **Top-5 #3 / F-IP** |
| S6 | **Library Agent Tools**（薄 MCP/Chat tools） | AIPOCH `library-mcp-server.ts` | 4 | 4 | **16** | **Top-5 #4 / F-Lit+** |
| S7 | **PubChem 完整 GHS**（`pubchem_get_safety`） | AIPOCH `connectors/descriptors/chemistry.ts` | 4 | 4 | **16** | **已入账 F-Chem-A**；补齐非爆炸物危害 |
| S8 | **PubChem similarity search**（库级 2D Tanimoto） | AIPOCH `pubchem_similarity_search` | 4 | 4 | **16** | **已入账 F-Chem-A**；FM 仅本地 pair，缺库搜索 |
| S9 | **Analytical method validation** skill | SynSci `skills/chemistry/analytical-method-validation` | 4 | 4 | **16** | **已入账 F-Lab**；化学品 QC / ICH Q2·Q14 |
| S10 | **Statistical power / MDE** | SynSci `skills/research/statistical-power` | 4 | 4 | **16** | **已入账残差**；DOE/QC 样本量先验 |
| S11 | **Uncertainty & units** | SynSci `skills/physics/uncertainty-and-units` | 4 | 4 | **16** | **已入账残差**；浓度/膜厚/VOC 等 ± 与单位 |
| S12 | **Checksum-bound PDF attachment authority** | AIPOCH `attachment-authority.ts` | 4 | 4 | **16** | **已入账残差**；字节漂移废 lease |
| S13 | **Artifact-bound literature corpus** | AIPOCH `artifacts/literature-manifest.ts` | 4 | 4 | **16** | **已入账残差**；报告版绑定检索范围 |

### Tier A — 得分 15（强推荐）

| # | 项 | 源 | 必要 | 可行 | 分 | 落点 / 备注 |
|---|----|----|------|------|----|-------------|
| A1 | **化学品 Evidence Golden Bench** | SynSci harness × FM golden | 5 | 3 | **15** | **Top-5 #1 / F-Quality**；`evals/chem_evidence/` |
| A2 | **Metadata Enrich**（裸 DOI 回填） | AIPOCH `metadata-enricher.ts` | 3 | 5 | **15** | **Top-5 #5 / F-Lit+** |
| A3 | **Analysis-report** skill | SynSci `skills/core/analysis-report` | 3 | 5 | **15** | **已入账残差**；贴 method-writer |
| A4 | **Export PARTIAL 终态** | SynSci `skills/research/export` | 3 | 5 | **15** | **已入账 F-Ops** |
| A5 | **`citations`/`sources` always-on** | AIPOCH `activation-policy.ts` | 3 | 5 | **15** | **已入账 F-Stamp-UX** |
| A6 | **DataCite + Zenodo** 研究数据 DOI | AIPOCH `literature-doi` / `zenodo.ts` | 3 | 5 | **15** | **已入账残差**；配方研究数据寄存 |
| A7 | **`compare` 先冻结判据** | SynSci `skills/research/compare` | 3 | 5 | **15** | **已入账 F-Lab** |
| A8 | **statistical-conventions** skill | SynSci `skills/core/statistical-conventions` | 3 | 5 | **15** | **已入账 F-Lab** |
| A9 | **LaTeX+bib 轻量包** | AIPOCH `latex-bundle.ts` | 3 | 5 | **15** | **已入账 F-Cite-Out** |
| A10 | **Library 标识符查重组硬化**（exact scheme union） | AIPOCH `duplicates.ts` | 3 | 5 | **15** | **第三次新记**；E-Lit 有合并，缺可辩护分组审计 |

### Tier B — 得分 12（值得排期）

| # | 项 | 源 | 必要 | 可行 | 分 | 落点 / 备注 |
|---|----|----|------|------|----|-------------|
| B1 | **Smart Screening LLM** | AIPOCH `smart-collections.ts` | 4 | 3 | **12** | **Top-5 #2 / F-Lit+** |
| B2 | **Harness composition manifests** | SynSci `docs/notes/harness-manifests.md` | 4 | 3 | **12** | **已入账残差**；prompt/tool schema 指纹 |
| B3 | **Research-contract preregistration** | SynSci research-workflows / session | 4 | 3 | **12** | **已入账残差**；试验前冻结分析计划 |
| B4 | **Reproducibility receipts**（表/图 compare，无 replay） | AIPOCH `artifact-reproducibility*` | 4 | 3 | **12** | **已入账残差**；DOE 图表 matched·different |
| B5 | **ChEMBL connector** | SynSci/AIPOCH chembl | 3 | 4 | **12** | **已入账 F-Chem**；助剂 bioactivity；次于 GHS |
| B6 | **Skill-load SHA 审计** | SynSci skill-runtime | 3 | 4 | **12** | **已入账 F-Ops** |
| B7 | **Install Layer-2 注入拒绝** | SynSci install review | 3 | 4 | **12** | **已入账 F-Ops** |
| B8 | **Provenance reason 细分类** | SynSci `provenance/envelope.ts` | 3 | 4 | **12** | **已入账 F-Ops** |
| B9 | **Acceptance-checks** skill | SynSci `skills/core/acceptance-checks` | 3 | 4 | **12** | **已入账残差**；工艺窗出口检查 |
| B10 | **Provenance review lifecycle** | SynSci `science/provenance/review.ts` | 3 | 4 | **12** | **已入账残差**；open→addressed→confirmed |
| B11 | **Stale-review on scope drift** | AIPOCH `reviewer/stale-reviews.ts` | 3 | 4 | **12** | **已入账残差** |
| B12 | **Citation fidelity**（导出保 locator） | AIPOCH `citation-fidelity*` | 3 | 4 | **12** | **已入账残差**；≠ CSL 排版 |
| B13 | **Smart screening rule history** | AIPOCH `smart-rule-history.ts` | 3 | 4 | **12** | **已入账残差**；并进 B1 |
| B14 | **多源 full-text finder** | AIPOCH `full-text-finder.ts` | 3 | 4 | **12** | **已入账 F-Lit-Deep** |
| B15 | **CSL / format_references** | AIPOCH `citation-formatter.ts` | 3 | 4 | **12** | **已入账 F-Cite-Out** |
| B16 | **prepared-literature sidecar** | AIPOCH sidecar | 3 | 4 | **12** | **已入账 F-Stamp-UX** |
| B17 | **冲突感知 metadata supplement** | AIPOCH `duplicate-metadata.ts` | 3 | 4 | **12** | **第三次新记**；合并时保留冲突标记 |
| B18 | **Crossref/PMID reference-resolver 加深** | AIPOCH `reference-resolver.ts` | 3 | 4 | **12** | **第三次新记**；DOI 失败走 PMID/Crossref |
| B19 | **RIS/BibTeX citation-exchange 保真** | AIPOCH `citation-exchange.ts` | 3 | 4 | **12** | **第三次新记**；E-Lit 已有导出，缺边界校正 |
| B20 | **PubChem synonyms / batch get_compounds** | AIPOCH chemistry tools | 3 | 4 | **12** | **第三次新记**；原料同义与批量属性 |
| B21 | **Dimensional analysis / pint 单位** | SynSci `skills/physics/dimensional-analysis` | 3 | 4 | **12** | **第三次新记**；工艺窗量纲一致性 |
| B22 | **ScholarEval 加深 peer-review** | SynSci `skills/scholar-evaluation` | 3 | 4 | **12** | **第三次新记**；FM 已有 peer-review，可加结构化维度 |
| B23 | **RDKit Chat Skill 包**（高级操作纪律） | SynSci `skills/chemistry/rdkit` | 3 | 4 | **12** | **第三次新记**；FM 有 chemtools API，缺对话纪律 |
| B24 | **Literature-review exclusion ledger** | SynSci/AIPOCH lit-review 加强 | 3 | 4 | **12** | **第三次新记**；系统综述排除账本 |
| B25 | **Hypotheses → DOE 可证伪帧** | SynSci `skills/core/hypotheses` | 3 | 4 | **12** | **第三次新记**；贴现有 DOE，非取代 |
| B26 | **`/review` 证据优先复核工作流** | SynSci `skills/research/review` | 3 | 4 | **12** | **第三次新记**；与 reviewer rubric 互补 |

### Tier C — 得分 9（可后置）

| # | 项 | 源 | 必要 | 可行 | 分 | 落点 / 备注 |
|---|----|----|------|------|----|-------------|
| C1 | **批量 Library jobs / journal** | AIPOCH `batch-jobs.ts` | 3 | 3 | **9** | **已入账 F-Lit-Deep** |
| C2 | **本地 PDF passage index** | AIPOCH `full-text-index.ts` | 3 | 3 | **9** | **已入账 F-Lit-Deep**；加强 locator |
| C3 | **Agent PDF inbox** | AIPOCH `agent-pdf-acquisition.ts` | 3 | 3 | **9** | **已入账 F-Lit-Deep** |
| C4 | **Molecule preview** | AIPOCH `connectors/molecule` | 3 | 3 | **9** | **已入账 F-Chem-A**；UI 成本 |
| C5 | **RO-Crate complete（附 PDF 字节）** | AIPOCH `ro-crate-export.ts` | 3 | 3 | **9** | **已入账 F-Stamp-UX**；D2 lite 已够 |
| C6 | **Document reader**（有界 PDF 抽取给 Agent） | AIPOCH `document-reader.ts` | 3 | 3 | **9** | **第三次新记**；可贴 C2 |
| C7 | **matchms 光谱鉴定** skill | SynSci `skills/chemistry/matchms` | 3 | 3 | **9** | **第三次新记**；QC 质谱/光谱比对；偏实验室 |
| C8 | **配方多目标 / Pareto 纪律**（改编非药化） | SynSci multi-objective-optimization 思路 | 3 | 3 | **9** | **第三次新记**；FM 已有 tradeoff；Skill 纪律层 |
| C9 | **Scientific output comparison** | AIPOCH `scientific-output-comparison.ts` | 3 | 3 | **9** | **第三次新记**；与 receipts 重叠时可合并 |

### Tier D — 得分 6–8（边际）

| # | 项 | 源 | 必要 | 可行 | 分 | 备注 |
|---|----|----|------|------|----|------|
| D1 | Library catalog capacity / scale guards | AIPOCH `catalog-capacity*` | 2 | 4 | 8 | 大库防护；非主路径 |
| D2 | figure-style skill | AIPOCH `figure-style` | 2 | 4 | 8 | 写作抛光 |
| D3 | ChEBI ontology relations 加深 | AIPOCH `chebi_get_ontology` | 2 | 4 | 8 | FM 已有 ChEBI lookup |
| D4 | DOCX `{{cite}}` | AIPOCH `citation-document.ts` | 2 | 3 | 6 | **已入账 F-Cite-Out** 低优先 |
| D5 | Rhea 反应 | AIPOCH rhea_* | 2 | 3 | 6 | **已入账 F-Chem-A** 弱相关 |
| D6 | ZINC 可购 | AIPOCH/SynSci zinc | 2 | 3 | 6 | **已入账**；配方原料可购性弱匹配 |
| D7 | PubChem bioassay summary | AIPOCH | 2 | 3 | 6 | 偏药理 |
| D8 | openFDA / Drugs@FDA | AIPOCH drug-regulatory | 2 | 3 | 6 | 偏药政；特种化学品弱 |
| D9 | Market-research-reports（50+ 页 LaTeX） | SynSci | 2 | 3 | 6 | 重；竞品扫优先走 Smart Screening |
| D10 | pymatgen 材料信息学 | SynSci | 2 | 3 | 6 | 固体材料伸展，非配方主轴 |
| D11 | execution-hygiene | SynSci | 2 | 3 | 6 | 偏长作业/HPC |
| D12 | ISO standards readiness | SynSci | 2 | 3 | 6 | 合规文案；非钢印主轴 |
| D13 | PMID/PMCID 导入 | AIPOCH/SynSci | 2 | 3 | 6 | 偏生医；ChemRxiv/DOI 优先 |

### 边界外 / 明确不借（第三次重申，不评分）

| 类别 | 代表 | 原因 |
|------|------|------|
| 桌面 / 运行时 | Electron · ACP 多 backend · WSL sandbox · Prisma 桌面库 | 栈错位 |
| 计算环境 | Notebook/SSH/Slurm/HPC · remote-compute · cloud-compute 全家桶 | 产品边界外 |
| 生态 | Skills Marketplace · 全量 Specialist · skill-installer | 运营面过大 |
| 文献栈错位 | **arXiv** · 全量 `pdf-structure` · PDF 批注工作台 · Zotero | 已定 ChemRxiv；UX/协议成本 |
| 生物 / 结构预测 | AlphaFold/Boltz/ESMFold/OpenFold · scvi · 基因组学连接器 | 非化学品配方 |
| 药化计算 | docking / MD / DiffDock / BindingDB 主路径 · BioNeMo · denovo-design | 弱相关或栈重 |
| 榜单 | Harbor / DrugDiscoveryBench / cadence 生物 KPI | 不当产品门禁 |
| 重叠已厚 | paper-lookup 18 API 全家桶 · 再接一层与 OpenAlex 重复的文献源 | 维护面＞增益 |

### 第三次深扫相对二次的增量（仅新记）

| 增量 | 建议归桶 |
|------|----------|
| Library 查重组硬化 · 冲突感知 merge · reference-resolver · citation-exchange 保真 | 并进 **F-Lit+** / Metadata Enrich |
| PubChem synonyms / batch properties | 并进 **F-Chem-A** |
| Dimensional analysis · ScholarEval · RDKit Skill · exclusion ledger · Hypotheses · `/review` | Skill 快插；可贴 F-Lab / F-Quality / F-Lit+ |
| Document reader · matchms · 配方 Pareto 纪律 · scientific-output-comparison | Tier C；按实验室/DOE 偏好点名 |

### 建议勾选方式

1. 先锁定打包：**F-Quality** / **F-Lit+** / **F-IP** / **F-Chem** / **F-Chem-A** / **F-Lab**（可多选）。  
2. 在 Tier S→B 内点名子集（或「Tier S 全做」）。  
3. Tier C/D 默认不排期，除非你点名。  
4. 确认后回复「写详细方案再开工」或「先做 Sx,Ay,…」。

## 请评估

1. 是否锁定 **F-Quality = 1** 与/或 **F-Lit+ = 2→5** / **F-IP = 3**？  
2. 是否插队 **F-Chem**（SynSci SMILES + fetch-outcome）/ **F-Chem-A**（AIPOCH GHS+similarity）或 **F-Lab**（analytical-method-validation，全化学品 QC/ICH）？  
3. Top-5 内想先做哪几项（可点名子集）？  
4. 是否采纳 **全量目录** 中第三次增量（查重硬化 / PubChem synonyms / dimensional-analysis / exclusion ledger 等）？  
5. Golden Bench 目标题量：20 种子 or 直接冲 50？（种子按 `ProductDomain` 分桶，非 coatings-only）  
6. 确认后回复「写详细方案再开工」或点名子集（可用 S#/A#/B#）。
