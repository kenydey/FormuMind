# 评估：nashsu/llm_wiki 与 FormuMind LLM Wiki — 整合 / 借鉴 / 二次开发

状态：**评审结论（严苛）— 不整合仓库；可选极窄「思想借鉴」重写**  
日期：2026-09-14  
对照对象：本地克隆 `vendor/llm_wiki`（GPL-3.0，**勿入库**）  
FormuMind 主轨：W0–W4 → S1/P2/P3/Ops → P4 Dossier → P5 Report → P4.6/#95/#96  
ADR：[`../architecture/ADR-2026-09-13-wiki-compiled-memory.md`](../architecture/ADR-2026-09-13-wiki-compiled-memory.md)

---

## 0. 一句话结论

| 问题 | 结论 |
|------|------|
| **要不要把 `llm_wiki` 整合进 FormuMind？** | **不要。** 产品目标、信任模型、运行时栈、许可证四线冲突，整合必成缝合怪。 |
| **有没有值得借鉴的点？** | **有，但少且必须重写。** 仅 L2/运维体验级思想，禁止搬运行时代码与「LLM 写全 Wiki」范式。 |
| **必要性** | **整仓整合：极低。** FormuMind 工业配方闭环 Wiki 已落地且边界清晰。 |
| **可行性（整仓）** | **低。** GPL-3 传染 + Tauri/LanceDB/Obsidian 心智 vs FastAPI/SQLite/Claims·DOE。 |
| **可行性（思想借鉴）** | **中高。** 选 0–2 个切片，按 FormuMind ADR 自研，不依赖其源码。 |

---

## 1. 两边到底是什么（先对齐对象）

### 1.1 FormuMind LLM Wiki（现网）

- **产品**：金属表面处理 / 配方 R&D 闭环（要求→文献→推荐→DOE→台账→寻优→卷宗→报告）。
- **架构**：L0 Raw（Claims SSOT）→ L1 **确定性**实体 Wiki → L2 可选 LLM theme/dossier → Report 草稿。
- **SSOT**：`data/wiki` + `wiki_pages`；**App-native Reader**（明确拒绝 MkDocs/Obsidian 运行时）。
- **信任**：Claims **只 Raw**；DOE **软 hint 只 L1**，永不静默吃 L2/Report。
- **栈**：Python FastAPI + Celery + React Hub；检索复用 `document_chunks`（禁第二向量库）。

### 1.2 nashsu/llm_wiki（对照）

- **产品**：跨平台**个人知识库桌面应用**（研究笔记 / 阅读 / 成长 / 泛商业模板）。
- **范式**：忠实 Karpathy：Raw 不可变 → **Wiki 几乎全由 LLM 写入维护** → Schema/purpose 约束。
- **SSOT**：本地 Markdown vault，**Obsidian 兼容**；index.md / log.md 导航。
- **栈**：Tauri 2 + React + **Rust** Chat Agent + 可选 **LanceDB** 向量；MCP/本机 HTTP API；Chrome clipper。
- **许可证**：**GPL-3.0**（`vendor/llm_wiki/LICENSE`）。

两者都「编译记忆而非纯 RAG」，但 **FormuMind 是受监管的工业证据链产品；llm_wiki 是个人知识工作台。** 同词「Wiki」不等于可合并。

---

## 2. 功能对照（严苛：重叠 / 冲突 / 缺口）

| 能力 | llm_wiki | FormuMind | 判定 |
|------|----------|-----------|------|
| Raw→Wiki 编译记忆 | ✅ 核心 | ✅ 已有（W0–W4 + P4） | **重叠，FM 已覆盖主价值** |
| LLM 写实体/概念页 | ✅ 默认主路径 | ❌ L1 禁止 LLM 洗数值；L2 旗标关 | **硬冲突（ADR）** |
| 两步 CoT ingest | ✅ Analysis→Generate | L1 确定性；L2 叙述较弱 | **可借鉴思想 → 仅限 L2** |
| index.md / log.md | ✅ | SQLite 列表 + FTS + Hub | FM 已有机器索引；人/LLM 目录可选手写 |
| FTS / 语义检索 | FTS + LanceDB | FTS5 + 可选 embed 进 **现有** chunks | **禁 LanceDB 第二库** |
| Chat 双轨 / Raw-only | ✅ Source-only | ✅ blend + raw_first | 重叠 |
| 知识图谱 | 4-signal + Louvain（页图） | Neo4j KG（配方/实测拓扑）+ Hub 图谱探针 | **不同图，勿混** |
| Deep Research | 内置搜网→ingest | 已有 research/search/ingest | 重叠 |
| 异步 Review 队列 | ✅ 强（预置动作/搜问） | 弱（flag + 标记已审） | **可选借鉴 UX** |
| Dossier / DOE / 台账 / Report 导出 | ❌ 非产品域 | ✅ P4/P5/P5.1 | **FM 独有，llm_wiki 帮不上** |
| Claims / 审计边界 | 弱（个人库） | 强（代码+测试锁死） | **整仓引入会稀释边界** |
| 多模态图注 / Mermaid | ✅ 强 | 部分（OCSR/Reader） | 体验可借鉴，非刚需 |
| 桌面 Agent + shell | ✅ Rust agent | Web + Celery | **安全与部署模型不兼容** |
| MCP / 本机 API | ✅ | 无对等 | 非 FM 当前优先级 |
| Obsidian 双开 | ✅ 卖点 | ADR **否决**为运行时 | **硬冲突** |

---

## 3. 必要性评估

### 3.1 整仓整合 — **不必要**

1. FormuMind 已完成「编译记忆」主航道，并绑定 **项目卷宗 + Report + DOE/Claims**——这是 llm_wiki **没有**的工业闭环。  
2. llm_wiki 的差异化（桌面 Agent、Obsidian、LanceDB、个人 Deep Research UI）**不解决** FormuMind 当前产品缺口（灰度手测、Claims/DOE 解锁需评审、人编编辑器需开题）。  
3. 用户痛点若是「Wiki 不够聪明」：根因更可能是 **L2 默认关、auto_patch 关、叙述质量**，不是缺少第二套桌面 Wiki 壳。

### 3.2 思想借鉴 — **低–中必要性，且须克制**

仅当产品确认下列痛点之一时，才值得开切片：

| 痛点 | 借鉴点 | 优先级 |
|------|--------|--------|
| L2/卷宗叙述单次 LLM 抖动大 | 两步 CoT（先结构化分析，再写叙述；**不动表**） | P1（若开 LLM 叙述灰度） |
| Hub Lint 不可操作 | Review 队列式 UX（冲突/孤儿/缺页 → 预置动作） | P2 |
| LLM 编译 theme 找不到「全库地图」 | 可选生成 `wiki/catalog.md`（由索引导出，非第二 SSOT） | P3 |
| Reader 缺 Mermaid | 前端渲染增强 | P3（锦上添花） |

**非痛点、勿借**：LanceDB、Obsidian 运行时、LLM 重写 L1、Tauri 壳、GPL 源码、Chrome clipper 作为核心路径。

---

## 4. 可行性评估

### 4.1 法律 / 许可 — **整仓不可行**

- `llm_wiki` = **GPL-3.0**。  
- 将 GPL 代码链入 FormuMind（无论 submodule 还是复制）会迫使**整体传染**或触发合规灾难。  
- **允许**：读其文档与行为，用自己的许可证 **重写**思想；**禁止**：复制 `src/lib/ingest.ts` 等实现、vendor 进发行物。  
- 本地克隆仅供评审：路径 `vendor/llm_wiki/` 已列入 `.gitignore`。

### 4.2 架构 — **整仓不可行；局部重写可行**

| 若强行整合 | 后果 |
|------------|------|
| 引入 LLM 默认写实体页 | 撕毁 ADR「L1 确定性」；Claims/DOE 测试与审计失效 |
| 引入 LanceDB | 违背「不新建第二向量库」；与 chunk RAG 双真相 |
| 引入 Obsidian/Tauri 主路径 | 与 Hub/Chat/DOE Web 闭环割裂；运维双栈 |
| 共用 ingest 管道 | FM 已有 KB ingest + wiki compile；再挂 3.5k 行 TS ingest = 缝合怪 |

局部重写可行条件：

1. 只动 **L2 dossier_narrative / theme** 或 Hub Lint UX；  
2. 不新增向量产品、不新桌面运行时；  
3. 旗标默认关；  
4. 单测锁住 Claims/DOE 边界不回退。

### 4.3 工程成本（粗估）

| 方案 | 人日 | 风险 |
|------|------|------|
| 整仓整合 / 子应用 iframe | 30–60+ | 极高（许可+边界+双栈） |
| 子进程调 llm_wiki CLI | 10–20 | 高（GPL、数据模型不合、运维地狱） |
| 借鉴：L2 两步叙述 | 2–4 | 低–中 |
| 借鉴：Lint/Review 操作队列 | 3–5 | 低 |
| 借鉴：catalog.md 导出 | 1–2 | 低 |
| 什么都不做 + 灰度手测 | 0.5–1 | 最低，**当前最优 ROI** |

---

## 5. 明确否决（防缝合怪清单）

下列任一落地即视为架构失败：

1. 把 `llm_wiki` 作为 FormuMind 依赖或 submodule 发行。  
2. 默认让 LLM 覆盖 `materials/` / `chemicals/` / `systems/` 数值与 bounds。  
3. 新增 LanceDB（或任何第二向量库）专供 Wiki。  
4. Obsidian / MkDocs 成为在线 SSOT 或主 UI。  
5. Chat Agent 开放任意 shell 作为配方研发默认能力。  
6. 用 llm_wiki 的页图替换 Neo4j 配方 KG，或两套图无解释混用。  
7. Report/Dossier 叙述进入 Claims 或 DOE 硬边界。

---

## 6. 若仍要「升级 Wiki」——推荐实施方案（非整合）

### 阶段 0（立刻，推荐）

- **不写代码整合。**  
- 按 [`2026-09-14-wiki-hub-dossier-handtest.md`](./2026-09-14-wiki-hub-dossier-handtest.md) 开旗标灰度；证明现网价值。  
- 保留 `vendor/llm_wiki` 仅作只读参考（gitignore）。

### 阶段 A（可选，产品确认「叙述质量」后）

**目标：** 提升 L2 叙述，不碰 L1 表。

1. 在 `dossier_narrative` / `theme` 增加 **两步调用**：  
   - Step1：只输出 JSON（实体、矛盾、建议更新节、source_ids）；  
   - Step2：只写叙述段落；`validate_narrative` 继续禁表/禁伪图。  
2. 失败回退旧叙述；旗标仍 `wiki_dossier_llm_narrative`。  
3. 回归：现有 Claims/DOE + narrative 单测。

**明确不抄：** `ingest.ts` 两步写全库实体页。

### 阶段 B（可选，运维体验）

- 扩展现有 `lint.py` + Hub：孤儿页、冲突、缺链 → **可点击动作**（打开页 / 标记已审 / 触发 theme compile），而非另建 review 子系统。  
- 数据仍在 `wiki_pages.flags`。

### 阶段 C（明确不做，除非推翻 ADR）

- 个人知识库桌面壳、Obsidian 双写、LanceDB、GPL 代码移植、MCP 对外暴露配方库。

---

## 7. 决策矩阵（拍板用）

| 选项 | 建议 |
|------|------|
| A. 整仓整合 llm_wiki | **否决** |
| B. 子应用/iframe 嵌桌面 Wiki | **否决** |
| C. 许可合规下复制源码「二次开发」 | **否决**（GPL） |
| D. 思想借鉴 + FormuMind 自研窄切片 | **有条件通过**（先灰度，再最多 A/B） |
| E. 不借鉴，只做现网灰度与文档清理 | **默认推荐** |

---

## 8. 总结

`llm_wiki` 是优秀的 **Karpathy 模式桌面实现**，在个人知识管理上功能很全；FormuMind Wiki 是 **工业配方研发的编译记忆层**，已与 Claims/DOE/卷宗/报告焊死。  

**整仓整合既无必要也不可行**（许可 + ADR + 栈 + 信任模型）。  
**实用性**上，唯一站得住的升级是：在 **不破坏 L1/Claims/DOE** 的前提下，可选吸收「两步叙述」与「可操作 Lint」——且必须自研，禁止缝合。  

当前最高 ROI：**灰度跑通现网 Dossier/Report**，而不是引进第二个 Wiki 产品。

---

## 附录 A. 本地对照路径

```text
vendor/llm_wiki/          # git clone；.gitignore；勿 commit
backend/app/services/wiki/
docs/architecture/ADR-2026-09-13-wiki-compiled-memory.md
docs/plans/2026-09-14-wiki-project-dossier.md
```

## 附录 B. 克隆命令（评审用）

```bash
git clone --depth 1 https://github.com/nashsu/llm_wiki.git vendor/llm_wiki
```
