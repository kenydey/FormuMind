# 评估更新：nashsu/llm_wiki v0.6.11 × FormuMind LLM Wiki（可借鉴方案排序）

> 状态：**评审结论（2026-09-20）**  
> 对照：本地 `vendor/llm_wiki`（`git clone --depth 1`，**GPL-3.0，已在 `.gitignore`，勿入库**）  
> 前序：[`2026-09-14-llm-wiki-integration-eval.md`](./2026-09-14-llm-wiki-integration-eval.md)  
> ADR：[`../architecture/ADR-2026-09-13-wiki-compiled-memory.md`](../architecture/ADR-2026-09-13-wiki-compiled-memory.md)

---

## 0. 一句话结论

| 问题 | 结论 |
|------|------|
| 整仓整合 / 复制源码？ | **否。** GPL-3 传染 + Tauri/LanceDB/Obsidian/「LLM 写全 Wiki」与 FM ADR 四线冲突。 |
| 思想借鉴？ | **可，且须自研重写。** 只借协议/UX/信息架构，不借运行时栈。 |
| 前序已吸收？ | **两步卷宗叙述 + 可操作 Lint** 已落地；本轮重评剩余切片。 |
| 最高 ROI | 仍优先 **现网灰度（Dossier/Report）**；Wiki 增强选下表 **匹配度 ≥ ★★★★** 的 1～2 刀。 |

---

## 1. 对象对齐（2026-09-20）

### 1.1 llm_wiki（v0.6.11）

- **定位**：个人知识库桌面应用（Karpathy 编译记忆产品化）。
- **栈**：Tauri 2 + React + Rust Agent + 可选 LanceDB；本机 HTTP（19828）+ Chrome clipper（19827）+ MCP 薄封装。
- **范式**：Raw 不可变 → **LLM 默认写 Wiki 页**（两步 Analysis→Generate，`---FILE---` / `---REVIEW---`）→ `index.md` / `log.md` / `overview.md` 由 App 维护。
- **亮点**：持久 ingest 队列、Review 队列+sweep、结构/语义 Lint、四信号图+Louvain+insights、Deep Research→再摄入、多模态图注、Mermaid/KaTeX。

### 1.2 FormuMind Wiki（现网）

- **定位**：工业配方 R&D **编译记忆层**（要求→检索→推荐→DOE→台账→寻优→**卷宗→Report**）。
- **栈**：FastAPI + Celery + React Hub；`data/wiki` + `wiki_pages` SSOT；**禁**第二向量库 / Obsidian 运行时 / MkDocs 主路径。
- **信任**：L1 确定性；L2 theme/dossier/report **旗标默认关**；Claims 只 Raw；DOE 软 hint 不吃 L2。
- **已借思想（自研）**：`dossier_narrative` 两步；`lint.run` + Hub Flag 动作芯片；灰度门 + Report 冒烟。

---

## 2. 能力对照（重叠 / 冲突 / 仍可借）

| llm_wiki 能力 | FormuMind | 判定 |
|---------------|-----------|------|
| 两步 CoT 写实体页 | L1 **禁止** LLM 洗数值 | **硬冲突**（勿借到 L1） |
| 两步叙述 | 卷宗叙述 **已做** | 重叠，已吸收 |
| Review 队列 + 预置动作 | Lint flags + 芯片（较弱） | **可增强 UX** |
| 结构 lint（孤儿/断链） | `lint.py` 已有 | 重叠，可补「断链模糊建议」 |
| 语义 lint（LLM 矛盾） | 弱 | 可选，旗标关 |
| index/log/overview | FTS + Hub 列表 | 可选 **catalog 导出**（非 SSOT） |
| LanceDB + RRF | FTS5 + hybrid chunks | **禁 LanceDB**；思想已有探针/hybrid |
| 页图 Louvain / insights | Neo4j 配方 KG + Hub 图谱 | **不同图**；可借「缺口→行动」叙事 |
| Deep Research→再摄入 | 已有 research/ingest | 重叠；可借「结果落 wiki draft」 |
| Mermaid / 多模态 | Reader 部分；OCSR 另线 | Mermaid **可借**；PDF 图注非 Wiki 刚需 |
| Tauri Agent / shell / clipper / MCP | Web + Celery | **不做核心路径** |

---

## 3. 可实施方案（按与 FormuMind **核心功能匹配度**排序）

匹配度定义：对 **推荐 / DOE / Claims / 卷宗 / Report / Hub 运维** 的增益 ÷（ADR 风险 × 工程面）。  
一律：**自研重写 · 旗标默认关 · 不默认 LLM · 不碰 L1 数值表 · 不引入 GPL 代码**。

### ★★★★★ — 核心强相关（优先）

#### S1. Wiki Review / Lint 行动闭环加强（Hub） — **已落地（本分支）**

| 项 | 内容 |
|----|------|
| **借什么** | Review 队列式：矛盾 / 缺页 / 断链 → **预置动作**（打开页、补链建议、标记已审、触发 theme/卷宗刷新）；可选 sweep 清过期「缺页」 |
| **为何匹配核心** | 直接服务 **卷宗质量与 Claims 边界运维**：脏 L2 不进 DOE 的前提是人能快速审 Flag |
| **落点** | 扩展现有 `lint.py` actions + `HubWikiPane` 芯片；**不**新建第二 review 子系统 |
| **不做** | 另建 `review-store` 产品；LLM 自动改 L1 |
| **人日** | 2–4 |
| **验收** | Flag 行一键动作；单测锁 Claims/DOE 仍隔离 |
| **状态** | `broken` flag + `suggest_actions.target` + orphan 补链候选 + `POST /wiki/lint/sweep` + Hub「清过期 Flag」；无 L1 自动写链 |

#### S2. 确定性 `wiki/catalog.md`（或 API `GET /wiki/catalog`）供 L2 编译导航 — **已落地（本分支）**

| 项 | 内容 |
|----|------|
| **借什么** | llm_wiki 的 `index.md`「全库地图」角色——**由索引生成，App 维护，禁止 LLM 覆盖** |
| **为何匹配核心** | L2 theme / 可选 LLM 叙述缺「实体目录」时易胡写；卷宗/Report 溯源更稳 |
| **落点** | 从 `wiki_pages` 导出标题/path/kind/flags 列表；Hub 可下载；theme compile 可选注入 |
| **不做** | 第二 SSOT；Obsidian 双写 |
| **人日** | 1–2 |
| **验收** | 重建 catalog == DB 列表；编译注入可关 |
| **状态** | `GET/POST /wiki/catalog[/rebuild]` + 落盘 `catalog.md`（不 upsert wiki_pages）；`wiki_catalog_inject_themes` 默认关；Hub「重建/下载 Catalog」 |

---

### ★★★★ — 核心相关（灰度后值得）

#### S3. Wiki Reader Mermaid（+ 可选 KaTeX 补强）

| 项 | 内容 |
|----|------|
| **借什么** | 流程/因果图渲染（deck/卷宗叙述常含图） |
| **为何匹配** | 增强 **Report/卷宗可读性**（汇报给工艺/客户），不改信任模型 |
| **落点** | `WikiMarkdownReader` 懒加载 Mermaid；失败降级代码块 |
| **人日** | 1–2 |
| **验收** | 样例 theme 含 mermaid 可渲染；无 XSS |

#### S4. Chat / Deep Research 结果「存为 Wiki 草稿」（`queries/` 或 `themes/draft-*`）

| 项 | 内容 |
|----|------|
| **借什么** | `chat-save-to-wiki` / research→`wiki/queries/`→再编译 |
| **为何匹配** | 把研究结论 **沉淀进项目记忆**，服务后续卷宗 S2/S8 与 Report |
| **落点** | 旗标 `wiki_chat_save_draft`；只写 L2 草稿 + `unreviewed`；**绝不**进 Claims |
| **人日** | 2–3 |
| **验收** | 草稿 path 前缀隔离；DOE bounds 测仍忽略 drafts |

#### S5. 结构 Lint：断链模糊建议 + 一键补 `related` — **已落地（本分支）**

| 项 | 内容 |
|----|------|
| **借什么** | `lint-structural` 的 fuzzy suggest / apply fix |
| **为何匹配** | 降低卷宗/实体页孤岛，提升 Chat blend 命中 |
| **落点** | `lint.py` + Hub 动作「建议链到…」 |
| **人日** | 1–2 |
| **验收** | 样例 theme 含 fuzzy 芯片；显式点击才 rewrite / 补 Related；Claims/DOE 不变 |
| **状态** | `suggest_broken_fixes` + `POST /wiki/lint/apply-broken` + Hub「改链→」芯片；无 L1 数值/Claims 写入 |

---

### ★★★ — 可做但非刚需

#### S6. Wiki 页图「缺口洞察」→ 建议行动（轻量）

| 项 | 内容 |
|----|------|
| **借什么** | graph insights（孤立/稀疏 → Deep Research）叙事 |
| **为何匹配** | 辅助 **补文献/刷新卷宗**，但勿与 Neo4j 配方 KG 混 UI |
| **落点** | 基于 wikilink 邻接的启发式列表（无 Louvain 也可）；Hub Wiki 侧栏「缺口」 |
| **人日** | 2–4 |
| **风险** | 与材料 KG 双图认知负担 → 必须标注「Wiki 链接图」 |
| **完整实施计划** | 已展开为 [`2026-09-20-wiki-page-graph-viz.md`](./2026-09-20-wiki-page-graph-viz.md)（P0 画布+API → P1 洞察 → P2 材料 KG 画布） |

#### S7. 垂直 `purpose` / schema 提示词包（涂料/硅烷）

| 项 | 内容 |
|----|------|
| **借什么** | `purpose.md` + schema 类型路由表 |
| **为何匹配** | 已有 `vertical_addendum`；可整理成可切换包 |
| **落点** | 配置化 prompt 片段，不改编译内核 |
| **人日** | 1–2 |

---

### ★★ 及以下 — 明确不排期（除非推翻 ADR）

| 项 | 理由 |
|----|------|
| LLM 默认写 L1 实体/概念页 | 撕毁 Claims/DOE 审计 |
| LanceDB / 第二向量库 | ADR + 探针/hybrid 已覆盖检索调优 |
| Tauri 桌面壳 / Obsidian 双开 | 与 Web 闭环割裂 |
| Chrome clipper / MCP 作核心 | 非配方主路径；可后做边缘工具 |
| Rust Agent + shell.exec | 安全模型不兼容 |
| 整仓 GPL 移植 | 许可不可行 |

---

## 4. 建议落地顺序（与核心航道对齐）

```text
现网：Dossier/Report 灰度可观测（已基本齐）
  → S1 Review/Lint 行动闭环（运维，保信任边界）✓
  → S2 catalog 导出（喂 L2，不碰 L1）✓
  →（可选）S3 Mermaid / S4 草稿落盘
  → S5 断链模糊建议 + 显式改链 ✓
  → 不做 S★★ 以下
```

**默认推荐：** **S1 / S2 / S5 已落地**；下一 Wiki 刀可选 **S3 Mermaid** / **S4 草稿** 或灰度手测收口。

---

## 5. 法律与工程红线（重申）

1. `vendor/llm_wiki` **仅评审**；禁止 copy `src/lib/ingest.ts` 等进发行物。  
2. 允许：读行为与文档，用 FormuMind 许可证 **重写** 协议/UX。  
3. 任何切片必须：旗标默认关、单测锁 Claims/DOE、不新建向量 SSOT。

---

## 6. 附录

### A. 本地路径

```text
vendor/llm_wiki/     # v0.6.11；gitignore
backend/app/services/wiki/
frontend/src/components/knowledge-hub/HubWikiPane.tsx
```

### B. 克隆

```bash
git clone --depth 1 https://github.com/nashsu/llm_wiki.git vendor/llm_wiki
```

### C. 前序已落地（勿重复开题）

- 两步卷宗叙述：`dossier_narrative.py`  
- Lint + Hub 动作：`lint.py` / `HubWikiPane`  
- 灰度 / Report：`test_wiki_grayscale_gate.py` 等  
