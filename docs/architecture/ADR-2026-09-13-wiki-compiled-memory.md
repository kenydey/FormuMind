# ADR：Wiki Compiled Memory（拒绝 MkDocs 运行时，坚持单 SSOT + 应用内 Reader）

- **状态：** Accepted（2026-09-13）  
- **计划正文：** [`../plans/2026-09-13-wiki-compiled-memory.md`](../plans/2026-09-13-wiki-compiled-memory.md)  
- **父上下文：** W0–W4 LLM Wiki 混成已落地（`wiki_pages` + `data/wiki` + Chat blend + DOE soft）

---

## 背景

团队评估过「MkDocs + Material + Python 编译管道 + React iframe」以提升 Wiki 长文与阅读体验。现网已具备确定性编译记忆骨架，缺口主要是 **应用内呈现、内链、Chat 入口、可选主题长文与检索**，而非缺少第二套文档引擎。

## 决策

1. **不采用** MkDocs（或同类静态站）作为 FormuMind **运行时主呈现**（含 iframe 主路径）。  
2. **坚持** 单一 Markdown + `wiki_pages` SSOT；呈现用 React Reader（复用 `MarkdownMessage` + Wiki 层）。  
3. **内容分层 B+D：** L1 确定性实体/约束页为默认信任源；L2 `themes/` 可选 LLM 长文，默认关，不覆盖 L1 数值。  
4. **信任：** Claims 默认只 Raw；DOE 不静默吃 Wiki 硬边界；Chat 提供 Wiki/Raw/均衡模式，只调融合权重。  
5. **导出 / MkDocs 包** 现为非目标；若未来需要，仅作离线导出，不得回流为第二真相源。

## 理由

| 选项 | 结论 |
|------|------|
| MkDocs 运行时 | 第二构建与路由、iframe 孤岛、易双真相；与 Chat/DOE/Claims 产品路径割裂 |
| 外挂 CMS / Obsidian 发布 | 破坏「编译进仓 + source_ids」闭环 |
| 仅升级 Hub 原文墙 | 体感差，浪费已有 Markdown/KaTeX 能力 |
| **App-native Reader + 可选 L2** | 最小破坏、与现 API/旗标一致、工业审计可控 |

## 后果

- **正向：** 一套 SSOT；S1 即可显著改善可读性与导航；L2/FTS 可旗标演进。  
- **代价：** 需自建 wikilink/Reader/FTS，不能「免费」获得 Material 全家桶。  
- **约束：** 禁止默认 LLM 整页洗 L1；禁止 Wiki 替换 chunk RAG。

## 撤销条件

仅当同时出现：强制离线静态站交付、且产品接受双发布管道与审计成本上升，并经书面评审——才可重开「导出型 MkDocs」切片；**仍不得**将其升为在线 SSOT。

---

## 附录 A：Project Dossier（2026-09-14）

- **状态：** Accepted as L2 extension（旗标默认关）  
- **计划：** [`../plans/2026-09-14-wiki-project-dossier.md`](../plans/2026-09-14-wiki-project-dossier.md)  
- **主键：** `project_id`  
- **产物：** `themes/project-{id}.md` + 同路径旁路 `themes/project-{id}.data.json`  
- **自动 patch：** 默认关；手动 ensure/patch/refresh  
- **垂直 prompt：** 可选 `vertical_addendum`，不默认加载  
- **Report：** DossierPack 为上游；P5 MVP = 确定性 Markdown 草稿（`wiki_dossier_report_enabled`）+ 可选 LLM 执行摘要；**不得**把 Dossier/Report 叙述当 Claims 证据；PDF/Word 完整排版仍非目标  
- **约束延续：** 不引入 MkDocs 运行时；不覆盖 L1；不新建第二向量库  
