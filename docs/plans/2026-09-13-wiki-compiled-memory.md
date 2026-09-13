# Wiki Compiled Memory 升级方案（最终蓝图）

状态：**需求已冻结，待按阶段实施**（2026-09-13）  
父计划：[`2026-09-09-rag-llm-wiki-hybrid.md`](./2026-09-09-rag-llm-wiki-hybrid.md)（W0–W4 已落地）  
配套 ADR：[`../architecture/ADR-2026-09-13-wiki-compiled-memory.md`](../architecture/ADR-2026-09-13-wiki-compiled-memory.md)  
范围：在 **不破坏** 现有 Raw RAG / Claims / DOE 硬边界 / 单 SSOT Wiki 的前提下，补齐 **应用内阅读体验、导航、Chat 侧入口、可选 L2 主题长文与检索**；**拒绝 MkDocs 作为运行时主呈现**。

---

## 0. 已拍板决策表

| ID | 决策 |
|----|------|
| Q1 | 总蓝图 + 分阶段 DoD |
| Q1b | **Phase 1 = S1**：Reader + wikilink + 基础导航 |
| Q2 | Knowledge Hub 为主 + Chat 侧「相关 Wiki」 |
| Q3 | **B+D**：L1 事实 SSOT + 独立 `themes/`；`materials`/`chemicals` 严格 L1；`systems`/`mechanisms` 可 L2 伴侣页 |
| Q4 | 本阶段 **只读**；预留人编（override / 人审采纳）接口，实现后置 |
| Q5a | Claims：**行为只认 Raw**；蓝图写高门槛解锁条件，本阶段不实现 Wiki-as-Claim |
| Q5b | DOE：本阶段 **不**静默改硬边界；蓝图写「一键采纳 / 确认后变硬」解锁条件 |
| Q5c | Chat：用户可切换 **Wiki 优先 / Raw 优先 / 均衡** |
| Q6 | S1：Chat **标题列表 → 打开同一 Reader**（不做摘要卡、不做常驻双栏） |
| Q7 | 以 `MarkdownMessage` 为核 + Wiki 专用层（front-matter 卡、Flag 条、来源 chips） |
| Q8 | S1：`[[wikilink]]` 只链已有 `wiki_pages`；死链灰显；探因 / 严格语法后置 |
| Q9 | kind 筛选 + Flag + 左列表右正文 + **标题/path 子串搜索** |
| Q10 | 中文 UI；KaTeX + 表格 S1 必达；SMILES / 图片灯箱 Phase 2 |
| Q11 | L2 = Phase 2；旗标默认 **关**；手动/管理端触发；先做 **体系综述** 模板 |
| Q12 | S1 子串 → Phase 2 **元数据+正文 FTS 同切片** → Phase 3 Wiki 摘要向量 |
| Q13 | 静态导出（含 MkDocs 包）：**非目标**，评审后再说 |
| Q14 | **S1 一并**交付 Chat 模式切换 + 相关 Wiki 列表 |
| Q15 | L2 首模板 = 体系综述（`themes/system-*.md`） |
| Q16 | S1 DoD = **体验向 + 契约/测试向** 双达标 |
| Q17 | 非目标见 §6 |
| Q18 | 本文 + 短 ADR + 文首决策表 |

---

## 1. 一句话目标

把已落地的 **确定性编译 Wiki（L1）** 升级为产品内可用的 **Compiled Memory**：同一套 `data/wiki` + `wiki_pages` SSOT；Hub/Chat 共用可读 Reader；可选 L2 主题长文进 `themes/` 且默认关闭；检索与信任边界分阶段加强——**不引入第二文档站、不取代 Raw RAG**。

```text
Raw (chunks / sources)
        │
        ▼
L1 Entity Compiler（已有，确定性，SSOT）
        │
        ├──► React Wiki Reader（Hub + Chat 抽屉，本升级核心）
        │         + [[wikilink]] + 子串/FTS/向量（分阶段）
        │
        ▼
L2 Theme Compiler（Phase 2，可选，默认关）
        │     themes/ 体系综述等，引用 L1 + Raw，不覆盖 L1 数值
        ▼
Chat blend（模式可切换） / DOE soft hints
        │
Claims ← 仍只 Raw（解锁见 §5）
```

---

## 2. 与现状差距

| 能力 | 现状（W0–W4） | 本蓝图 |
|------|----------------|--------|
| 存储 / 编译 | `compile_source` → md + `wiki_pages` | **保持**；扩展 `themes/` 约定 |
| Hub UI | `HubWikiPane` 等宽原文 Markdown | **S1**：渲染 + 卡 + 搜 + 内链 |
| Chat | blend 已有；无「相关 Wiki」入口 | **S1**：列表开 Reader + 模式切换 |
| 长文 | 无 LLM Theme 层 | **P2**：旗标化 L2 |
| 搜索 | 列表/kind/Flag | S1 子串 → P2 FTS → P3 向量 |
| 导出 / MkDocs | 无 | **非目标**（运行时永不依赖） |

---

## 3. 内容模型（Q3 = B+D）

### 3.1 L1（严格 / 默认信任源）

| kind | 目录 | 规则 |
|------|------|------|
| material / chemical | `materials/` · `chemicals/` | **仅确定性编译**；禁止 LLM 覆盖数值、bounds、`source_ids` |
| system / mechanism / pitfall | `systems/` · `mechanisms/` · `pitfalls/` | L1 骨架（事实表、窗口、禁忌列表）继续确定性；长文见 L2 伴侣 |

### 3.2 L2（可选凝练，不进默认信任）

| kind | 目录 | 规则 |
|------|------|------|
| theme | `themes/` | LLM 主题长文；必须引用 L1 path + Raw `source_ids`；front-matter 含 `llm_generated: true`、`template`、`model`、`prompt_hash`、`reviewed: false` |
| 伴侣页（可选） | 如 `themes/system-{id}-narrative.md` | 与 L1 `systems/{id}.md` 成对；Reader 可「骨架 / 长文」切换 |

**铁律：** L2 **不得**静默写回 L1 的 bounds / 冲突裁决；Claims / DOE 硬边界默认 **不读** L2。

### 3.3 人编（仅预留，Q4）

- 接口预留：`human_override` 段 / `reviewed` 采纳流（B 或 C）。
- **S1 / P2 默认实现：只读。**

---

## 4. 分阶段交付

### Phase 1 — S1（本阶段承诺交付）

**范围**

1. **Wiki Reader 组件**（Hub 与 Chat 抽屉共用）  
   - 复用 `MarkdownMessage`（GFM 表、列表、KaTeX）  
   - Wiki 层：标题/path、Flag 条、front-matter 摘要卡、`source_ids` chips（点回 KB/Raw 若已有路由）  
2. **`HubWikiPane`**：kind + Flag + 左列表右 Reader + 标题/path **子串搜索**  
3. **`[[wikilink]]`**：解析为已有页（path / title / norm_key）；命中可跳转；未命中 **灰死链**（不可点或 tooltip「未编译」）  
4. **Chat**  
   - 相关 Wiki **标题列表** → 打开同一 Reader（抽屉或嵌入面板）  
   - **模式切换**：Wiki 优先 / Raw 优先 / 均衡（影响 blend 权重与提示策略；**不**改变 Claims 只 Raw）  
5. 保持只读 API；不改 L1 编译语义；不引入 MkDocs

**体验 DoD（Q16）**

- [x] Hub 打开任意非空 wiki 页：表格与公式可读，非等宽原文墙  
- [x] 子串搜索能过滤列表  
- [x] 页内已有 `[[存在页]]` 可跳转；不存在页灰显  
- [x] Chat 在 blend 开启且有命中时展示相关 Wiki 列表，点击打开与 Hub 一致的 Reader  
- [x] 模式切换三态可切换且刷新后续回答策略（flag 关时隐藏或 no-op）

**契约 DoD（Q16）**

- [x] 单测/组件测：wikilink 解析；模式枚举；flag 关时 Chat Wiki UI 不破坏旧路径  
- [x] 文档：本计划 S1 勾选 + ADR 链接  
- [x] Claims 路径回归：引用仍只来自 Raw Evidence  
- [x] DOE：无静默硬边界变更（与现网一致）

**非本阶段：** L2 编译、正文 FTS、向量、SMILES、死链探因、人编 UI、静态导出。

---

### Phase 2

1. **L2 Theme Compiler（Q11/Q15）**  
   - Flag：如 `wiki_llm_themes_enabled`（默认 **false**）  
   - 触发：管理端/手动 `compile_theme(system_id | topic)`  
   - 首模板：**体系综述** → `themes/system-{key}.md`  
   - 审计字段见 §3.2；默认 `reviewed: false`，Hub 可滤「仅已审」  
2. **Wiki FTS**：元数据（title/path/flags/kind）+ 正文 **同一切片**（Q12=C）  
3. wikilink 增强：死链「为何未编译 / 相关 Raw」；可选 `[[kind:key|label]]`  
4. SMILES / 结构式 / 图片灯箱（Q10）  
5. Chat 相关 Wiki 可升级为短摘要行（仍非常驻双栏）

**DoD（摘要）**

- [x] Flag 关：无 L2 页写入、无主题任务
- [x] Flag 开且手动跑：生成体系综述页，含 L1 链接与 Raw source_ids
- [x] FTS 可搜正文术语命中 wiki 页
- [x] L2 不进入 Claims；不进入 DOE 硬边界（constraints 跳过 `theme`）

实现记录：[`2026-09-13-wiki-p2-impl.md`](./2026-09-13-wiki-p2-impl.md)
---

### Phase 3

1. Wiki 摘要（或主题页）进入现有检索栈（`source_kind=wiki` 或等价），**仍双轨**，不关 chunk  
2. 人编 B/C 若产品需要再开题  
3. 导出 / MkDocs 包：仅当评审推翻 Q13 后另立切片  

**DoD（摘要）**

- [x] Flag 关：不写入 wiki 摘要 chunk、rebuild 拒绝  
- [x] Flag 开：摘要进入 `document_chunks`（`source_kind=wiki`），Chat Track A 可召回  
- [x] Track B / Claims 仍不把 Wiki 当 Raw；不新建第二向量库  

实现记录：[`2026-09-13-wiki-p3-impl.md`](./2026-09-13-wiki-p3-impl.md)

---

## 5. 信任边界与解锁条件

### 5.1 Claims（Q5a = D）

| 现在 | 解锁前不得做 | 解锁条件（全部满足才可开题评估） |
|------|----------------|----------------------------------|
| 证据链只认 Raw | Wiki 句作为可点击 Claim 证据 | ① 人审 `reviewed=true` ② ≥2 条独立 Raw 背书同一断言 ③ 无冲突/陈旧 Flag ④ 单独产品/安全评审 |

### 5.2 DOE（Q5b = D）

| 现在 | 允许讨论的远期 | 解锁条件 |
|------|----------------|----------|
| Soft hints only；硬边界不吃 Wiki | B：一键「采纳 Wiki 建议窗」到本次会话，可撤销；C：`lint=ok` 的 systems 经确认后写入硬边界 | ① L1 bounds 来源可解释 ② Flag 清空 ③ UI 明示来源 ④ 默认仍不静默 |

### 5.3 Chat（Q5c = C）

- 三模式只调 **检索融合与提示权重**，不调 Claims 规则。  
- Flag `wiki_chat_blend` 关：无模式切换或强制 Raw 路径。

---

## 6. 非目标（Q13 / Q17）

- MkDocs / Docusaurus / VitePress 等作为 **运行时主 UI** 或 iframe 主呈现  
- 静态导出（暂挂；评审后再说）  
- Neo4j **强依赖** Wiki 可读性  
- 人在线可视化编辑（仅预留契约）  
- 移动端专版、多租户 Wiki、公开外网知识站  
- **用 Wiki 替换 Raw RAG / 关掉 chunk 检索**  
- 默认对所有 L1 实体页做 LLM 整页重写  
- 为 Wiki 单独引入第二向量数据库产品线（P3 只复用现有管道）

---

## 7. 建议触及文件（实施时，非本提交改代码）

| 区域 | 候选 |
|------|------|
| 前端 Reader | 新 `WikiMarkdownReader.tsx`（或等价）；改 `HubWikiPane.tsx`；Chat 侧相关列表 + 模式切换 |
| 复用 | `MarkdownMessage.tsx` |
| API | 现有 `/api/wiki/*`；S1 子串可前端滤或薄查询参数；P2 FTS 新端点 |
| 编译 | `services/wiki/compile.py`（P2 增 theme）；`env_flags` |
| 测试 | 前端组件测 + `test_wiki_*` 回归；Chat claims 回归 |

---

## 8. 与既有计划关系

| 文档 | 关系 |
|------|------|
| `2026-09-09-rag-llm-wiki-hybrid.md` | 父架构；W0–W4 已完成；本文为其 **体验与 L2/检索** 续篇 |
| `2026-09-09-wiki-w0-w2-impl.md` / `w3-w4` | 已落地内核；本文不回滚其 SSOT/铁律 |
| ADR 同日文档 | 记录为何不采用 MkDocs 运行时、为何 B+D |

---

## 9. 实施开关（建议名，落地时以 `env_flags` 为准）

| Flag | 默认 | 阶段 |
|------|------|------|
| 现有 `wiki_*` | 保持现网 | — |
| `wiki_chat_mode_switch`（或并入 blend UX） | S1 随 UI | S1 |
| `wiki_llm_themes_enabled` | **false** | P2 |
| `wiki_fts_enabled` | **false**→P2 开 | P2 |
| `wiki_embed_enabled` | **false** | P3 |

---

## 10. 下一步

1. ~~评审蓝图与 ADR~~（已冻结）  
2. ~~S1 / P2 / P3 实施~~（已落地，见对应 `…-impl.md`）  
3. **运维收尾（本切片）**：Hub 重建 FTS/Embed、编译主题、标记已审；Q4 `reviewed` / `human_override` 契约预留（非完整编辑器）  
4. 人编 B/C 完整编辑器、MkDocs/静态导出：仅当产品明确开题或推翻 Q13 后另立切片  
