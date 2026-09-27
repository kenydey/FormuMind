# Post–Wave C OpenScience Top-5（供评估）

> 状态：**仅评估，未开工**（2026-09-27）  
> 源：`/tmp/openscience`（SynSci）· `/tmp/aipoch-open-science`（AIPOCH）  
> 对照：FormuMind 已合入 Wave A (#164) / B (#165) / C (#166 草案)

## 产品边界（不变）

- **要**：配方 R&D 闭环 + Evidence / 报告 **钢印诚实度**
- **不要**：Marketplace · Notebook/SSH/Slurm/HPC · 完整 ACP 多 backend · 完整 Literature Library UX · 全量 Specialist 子代理生态

## 已借（勿重复推荐）

| Wave | 项 |
|------|----|
| A | 导出 Preflight · Evidence Reviewer 有界修正 · MCP 勾选/会话审批 |
| B | PaperQA 解耦 · 文献 Manifest/冻结 · 轻量关键词筛选 |
| C | OpenAlex 引用扩展 · `notice_kind` · `evidence_provenance` · `citations` skill · ChEBI |

## 评分口径

- **必要性**（1–5）：对钢印诚实度 / 冻结语料可用性 / 配方证据链的直接增益  
- **可行性**（1–5）：可复用现有 FM 模块、不引新栈、2–5 人日量级可切片  
- **推荐序** = 必要性优先，同分看可行性与与主路径贴合度

---

## Top-5 推荐（Wave D 候选）

### 1. Sources 审计技能 + claim→passage 表（SynSci `core/sources`）

| | |
|--|--|
| **源** | SynSci `backend/cli/skills/core/sources/SKILL.md` |
| **必要性** | **5** — Wave C 的 provenance 停在 DOI/可用性层；钢印仍缺「每条论断对应哪段原文」的可交付审计表 |
| **可行性** | **5** — 新 bundled Chat Skill + 可选确定性汇总字段（复用现有 claim-check / `chunk_ids`）；不必上完整 Reviewer |
| **FM 落点** | `resources/chat_skills/sources/`；可选 `ChatResponse.sources_audit`；ResearchPanel 折叠表 |
| **切片** | D1a skill only → D1b 结构化表（supported/partial/unsupported/contradicted） |
| **明确不做** | 自动改写正文；完整 ACP Reviewer |

### 2. 引用页码/段落 Locator 诚实度（AIPOCH pdf-structure 纪律，轻量）

| | |
|--|--|
| **源** | AIPOCH `literature/pdf-structure/*`（纪律与 caption/page evidence）；非整引擎移植 |
| **必要性** | **5** — 数值工艺窗无页码时「看起来有出处」；钢印需要 locator 或显式「页码未知」 |
| **可行性** | **4** — FM 已有 `CitationAnchor.page` / chunk `page_no` / hybrid_parse；缺口在 Evidence 回答与 Preflight **强制暴露/门禁** |
| **FM 落点** | CitationChip 显示 `pp. N`；Preflight：裸数值且无 page/paragraph → warning/blocking（旗标） |
| **切片** | D2a UI+SSE 透传 → D2b Preflight 规则 |
| **明确不做** | 完整 figure/table layout 引擎、临床排版专用解析 |

### 3. 冻结语料 OA 批量补全（AIPOCH full-text-finder 流程 × FM `fulltext_fetcher`）

| | |
|--|--|
| **源** | AIPOCH `literature/full-text-finder.ts` + `full-text-sources.ts`（Unpaywall / EuropePMC） |
| **必要性** | **4** — Manifest/Freeze 已有，但候选常只有元数据；无 PDF 则 PaperQA/证据链空洞 |
| **可行性** | **5** — FM **已有** `fulltext_fetcher`（Unpaywall/PMC）；缺的是挂到 `literature_manifest` Capture/Freeze 的批量「补全文」动作 |
| **FM 落点** | Hub 冻结条「补全文」；`POST /api/wiki/literature/enrich-oa`；fail-open 计数 |
| **切片** | D3a 对 frozen/manifest 缺全文 DOI 批量 enrich → D3b 进度条/失败原因 |
| **明确不做** | 付费出版社绕过；完整 Literature Library |

### 4. 轻量 RO-Crate / 钢印可携包（AIPOCH lightweight RO-Crate）

| | |
|--|--|
| **源** | AIPOCH `artifacts/ro-crate-export.ts`（lightweight profile：元数据 + SHA-256，不强制整包字节） |
| **必要性** | **4** — 客户/合规要「带走」冻结文献 + 报告 + provenance/preflight 收据，而非仅 Markdown |
| **可行性** | **3** — 新导出适配器；可只做 lightweight（引用路径+checksum+provenance JSON），complete profile 后置 |
| **FM 落点** | Wiki/卷宗 Finalize 旁「导出 RO-Crate」；打 zip：`ro-crate-metadata.json` + 冻结 manifest + preflight + provenance |
| **切片** | D4a metadata-only crate → D4b 可选附带已落地 PDF 字节 |
| **明确不做** | 全会话 deterministic replay、`.science` 完整包、环境锁导入 |

### 5. Peer-review 技能 → STORM/卷宗草稿（SynSci `core/peer-review`）

| | |
|--|--|
| **源** | SynSci `backend/cli/skills/core/peer-review/SKILL.md`（BLOCKING vs OBSERVATION、校准推荐） |
| **必要性** | **4** — Preflight 抓脚注/占位符；方法论与「主张是否被数据支持」仍靠模型自由发挥 |
| **可行性** | **4** — 涂料/配方语境改写为 Chat Skill；可选 Hub「审稿一遍」按钮注入 skill（非 ACP 子进程） |
| **FM 落点** | `resources/chat_skills/peer-review/`；对接 dossier/STORM 草稿上下文 |
| **切片** | D5a skill → D5b 报告页一键审稿（输出 BLOCKING 列表，不自动改稿） |
| **明确不做** | 完整 ScholarEval 打分平台、自动重写终稿 |

---

## 对比总表

| # | 项 | 主源 | 必要性 | 可行性 | 建议旗标（默认） |
|---|----|------|--------|--------|------------------|
| 1 | Sources 审计 skill + claim 表 | SynSci | 5 | 5 | skill：`chat_skills_runtime`；表：`sources_audit_enabled`（建议默认开） |
| 2 | Locator 诚实度（页码/段落） | AIPOCH 纪律 | 5 | 4 | `citation_locator_preflight`（建议默认 warning） |
| 3 | 冻结语料 OA 批量补全 | AIPOCH 流程 × FM fetcher | 4 | 5 | `literature_oa_enrich_enabled`（默认开） |
| 4 | 轻量 RO-Crate 导出 | AIPOCH | 4 | 3 | `ro_crate_export_enabled`（默认关，浸泡后开） |
| 5 | Peer-review skill（卷宗） | SynSci | 4 | 4 | skill user-controlled |

## 候补（未进前 5）

| 项 | 原因 |
|----|------|
| BibTeX/RIS 自冻结集导出 | 可行性极高，但必要性弱于钢印/全文 |
| Smart screening LLM 升级 | Wave B 关键词已够 triage；LLM 成本高 |
| ChEMBL / SMILES-validation skill | FM 已有 PubChem/ChEBI/SureChEMBL/化学 API |
| experimental-design skill | FM 已有 DOE 闭环；增益边际 |
| Evidence 50 题 golden bench | 必要性高，但数据集与维护成本使可行性偏低（专波） |
| Ontology-term-resolution | 偏生命科学本体，涂料主路径弱 |
| 完整 PDF annotation / document notebook | UX 面大，偏离配方主路径 |

## 明确不借（重申）

Electron/ACP 多 backend · Notebook/SSH/Slurm · Skills Marketplace · Specialist 子代理市场 · `.science` 作为主交换路径 · 全量 pdf-structure 引擎

## 建议评估方式

1. 勾选拟做的 ID（建议默认锁定 **1→2→3**，**4/5** 二选一或并作次波）  
2. 确认旗标默认开/关与是否挡导出  
3. 回复「写详细方案再开工」或点名子集后实施
