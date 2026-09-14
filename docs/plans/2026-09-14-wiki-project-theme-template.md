# 项目技术 Wiki 模板（表面处理 / 配方 DOE）实施计划

> **已修订取代：** 请以 [`2026-09-14-wiki-project-dossier.md`](./2026-09-14-wiki-project-dossier.md) 为准。  
> 本文保留为早期动机与 MkDocs 可行性评审记录；P4 实施按 **Project Dossier** 八节卷宗 + 事件 patch。

状态：**历史草稿（被 Project Dossier 蓝图取代）**  
日期：2026-09-14  
父蓝图：[`2026-09-13-wiki-compiled-memory.md`](./2026-09-13-wiki-compiled-memory.md)  
ADR：[`../architecture/ADR-2026-09-13-wiki-compiled-memory.md`](../architecture/ADR-2026-09-13-wiki-compiled-memory.md)  
前序：S1 / P2 / P3 / Ops 已落地；L2 现仅有 `system_overview` 模板且默认关。

---

## 0. 对你这份脚本的可行性结论

**总体：方向可行，但不能原样落地。**  
「用 LLM 把项目 + DOE + 文献编译成工业实用技术 Wiki」应作为 **L2 Theme 新模板**接入 FormuMind；**MkDocs 运行时主呈现、用 LLM 覆盖 L1、用向量库当 Wiki 正文 SSOT** 均否决。

| 你方案中的能力 | 判定 | FormuMind 落点 |
|----------------|------|----------------|
| 固定 Markdown 工业模板（指标表 / 机理 / 配方 Wt% / DOE / 槽液） | ✅ 采纳 | L2 `themes/project-{key}.md`，模板 ID=`project_tech` |
| `[[原材料]]` / `[[Source:]]` / `[[DOE_Project:]]` 双链 | ✅ 采纳（语法对齐现网） | `[[chemical:…]]` / `[[material:…]]` + front-matter `source_ids` / `campaign_id` chips；Reader 已有 wikilink |
| 保留 `![](../images/…)` 多模态引用 | ✅ 采纳（需补资产管线） | 解析产物 asset 表 + Wiki 相对路径规范化；Reader 灯箱已有 |
| 配方表 Wt% / 替代料 | ✅ 采纳 | 从 campaign/配方/DOE 结构化字段填表，LLM 只润色叙述，**不得发明未背书比例** |
| 从 SQLite 拉实验 + DOE + RAG chunk | ⚠️ 改写 | 对接现有 `campaign` / DOE / `document_chunks` / L1 `wiki_pages` API，不新造 `experiments`/`doe_designs` 平行表 |
| SYSTEM_PROMPT 首席科学家编译器 | ⚠️ 收窄 | 仅 L2；输入=**结构化上下文包**，不是「把向量当正文」 |
| `mkdocs build` / `mkdocs serve` 作 Wiki 呈现 | ❌ 否决 | 违反 ADR：App-native Reader 是唯一运行时 SSOT 呈现 |
| LLM 输出直接当真相写入 materials/chemicals | ❌ 否决 | L1 仍确定性编译；L2 标 `llm_generated`，默认不进 Claims/DOE 硬边界 |
| 独立 `wiki_system/` + 第二套静态站 | ❌ 否决 | 继续 `data/wiki` + `wiki_pages` 单 SSOT |

一句话：

> **模板与工业结构要；MkDocs 壳不要；RAG/OCR 继续当燃料与证据；Wiki 正文 SSOT 仍是编译 Markdown；LLM 只写 L2 项目长文。**

---

## 1. 与现网架构对齐后的目标形态

```text
检索/上传 → 解析(markitdown/OCR) → Raw chunks(+向量)
                │
                ├─► L1 Compiler（已有，确定性）
                │      materials/ chemicals/ systems/ …
                │
                └─► P4 Project Theme Compiler（本计划，旗标默认关）
                       输入：Campaign/DOE 结构化结果
                            + 相关 L1 页
                            + Raw chunk 摘录（含图片路径）
                       输出：themes/project-{key}.md
                       呈现：Hub/Chat Wiki Reader（非 MkDocs）
                       检索：正文 FTS（已有）+ 可选摘要向量（P3 旗标）
```

信任边界（不变）：

- Claims：**只 Raw**
- DOE 硬边界：**不静默读 L2**
- L2 默认 `reviewed: false`；Hub 可滤「仅已审」

---

## 2. 冻结的页面模板（`project_tech`）

生成页必须符合下列骨架（与你的 SYSTEM_PROMPT 对齐，语法改为现网 wikilink）：

```markdown
---
kind: theme
template: project_tech
llm_generated: true
reviewed: false
campaign_id: "…"
source_ids: ["…", "…"]
l1_paths: ["systems/…", "chemicals/…"]
model: "…"
prompt_hash: "…"
---

# [[项目主题名称]]

> **研发方向**：… | **数据源**：Campaign `…` · Raw chips 见文末

## 1. 研究主题与技术要求
…

| 性能指标 | 技术要求 | 测试标准 |
| :--- | :--- | :--- |

## 2. 背景知识与复配机理
…（允许 KaTeX）
![](images/…png)   <!-- 必须保留上游资产路径，禁止虚构图 -->

## 3. 参考基准配方

| 原材料标准名 | 作用/角色 | 推荐比例 (Wt%) | 替代原料建议 | 引用来源 |
| :--- | :--- | :--- | :--- | :--- |
| [[chemical:kh-560|KH-560]] | … | 4.0 | … | source_id / DOE run |

## 4. 开发思路与 DOE 设计
…（只描述已发生的因子、边界、收敛；禁止编造未跑试验）

## 5. 技术路线与槽液维护
…
```

**硬规则（写进 compiler + 单测）：**

1. 配方行的比例 / CAS / 角色：优先来自结构化 DOE/配方表；LLM 不得新增无 `source_ids`/`run_id` 背书的数值。  
2. 图片：仅复制上下文包中已出现的 `![](...)`；缺失则写「（无图）」占位，禁止瞎编路径。  
3. 化学品名：尽量解析到已有 L1 `chemicals/` / `materials/`；没有则灰死链，可后置「建议编译实体」。  
4. 输出只能写入 `themes/project-*.md`，禁止写 `materials/`、`chemicals/`。

---

## 3. 数据装配（取代脚本里的假表）

`build_project_theme_context(campaign_id) -> ContextPack`：

| 字段 | 来源（现网） | 用途 |
|------|----------------|------|
| `project_meta` | campaign / 项目标题、领域、目标指标 | §1 |
| `spec_table` | 需求/目标规格结构化字段 | §1 表 |
| `baseline_formula` | 配方/推荐配方记录（成分、wt%、角色） | §3 表 |
| `doe_summary` | DOE 因子、边界、最优试验、迭代摘要 | §4 |
| `l1_pages` | 相关 `wiki_pages`（system/chemical/material） | 双链与机理骨架 |
| `raw_excerpts` | `document_chunks` Top-K（关键词/向量），**带 source_id 与图片引用** | §2 叙述与图 |
| `assets` | 解析阶段保存的 image 相对路径列表 | 强制保留 |

说明：

- **不是**「把 ColBERT 命中原文整页灌进 Wiki」。  
- **是**「结构化事实填表 + 少量 Raw 摘录喂 LLM 写叙述」。  
- OCR/markitdown 仍只在入库解析阶段跑；Theme Compiler **不重做 OCR**。

---

## 4. 分阶段实施（P4）

### P4.0 契约与旗标（0.5d）

- [ ] 新增旗标 `wiki_project_themes_enabled`（默认 **false**）；可与 `wiki_llm_themes_enabled` 组合或作为其子开关  
- [ ] 模板注册：`THEME_TEMPLATES = {system_overview, project_tech}`  
- [ ] front-matter schema：`campaign_id`、`template=project_tech`、`l1_paths`  
- [ ] 更新 ADR 附录：MkDocs 仍非运行时；本模板属 L2

**DoD：** 旗标关时 API 409；文档与 env_flags 可见。

### P4.1 ContextPack 装配（1–2d）

- [ ] `services/wiki/project_context.py`：按 `campaign_id` 拉配方/DOE/L1/Raw 摘录  
- [ ] 图片路径收集与规范化（统一到 `data/wiki/images/` 或既有资产根的相对路径）  
- [ ] 单测：无 campaign / 无 DOE / 无图片 三条降级路径

**DoD：** 给定夹具 campaign，ContextPack JSON 稳定可测。

### P4.2 Deterministic skeleton（1d，可无 LLM）

- [ ] 先用模板引擎把 §1/§3/§4 **表格骨架**写出来（零幻觉）  
- [ ] LLM 仅填充 §2/§5 叙述与衔接句；失败则保留骨架页

**DoD：** 关 LLM key 仍能生成可打开的 theme 页（表格完整）。

### P4.3 LLM 编译器（1–2d）

- [ ] Prompt = 本文件 §2 模板 + 工业规则（表面处理/水性防腐可作 system 附加）  
- [ ] 后处理：校验表头、剥离非法外链图片、wikilink 规范化、写入 `prompt_hash`/`model`  
- [ ] `POST /api/wiki/themes/compile` 扩展：`template=project_tech` + `campaign_id`

**DoD：** 金样 campaign → 页含五节 + 至少一行带 `[[chemical:…]]` 的配方表 + `source_ids` 非空。

### P4.4 Hub / Chat 体验（1d）

- [ ] Hub：Theme 列表可按 `project_tech` 滤；项目页入口「生成/刷新项目 Wiki」  
- [ ] Chat：相关 Wiki 命中 theme 时可打开 Reader；模式切换逻辑不变  
- [ ] 不引入 MkDocs iframe

**DoD：** 前端可触发编译并在 Reader 阅读；Claims 回归仍只 Raw。

### P4.5 质量门（并行）

- [ ] 单测：模板节齐全、禁止写 L1、旗标门闩、图片保留  
- [ ] 可选 lint：配方 wt% 合计≈100（软警告）  
- [ ] 文档：本计划勾选 + 与 `theme.py` 的 system_overview 并存说明

---

## 5. API 草图

```http
POST /api/wiki/themes/compile
{
  "template": "project_tech",
  "campaign_id": "cmp_…",
  "use_llm": true
}
→ 200 { ok, path: "themes/project-….md", reviewed: false }
→ 409 若旗标关闭
```

读取/搜索：复用现有 `GET /api/wiki/pages`、`/search`（FTS）、Reader。

---

## 6. 明确非目标（本 P4）

- MkDocs / 静态站作为运行时 UI（含 serve/build 热更新主路径）  
- 用 LLM 结果覆盖 L1 materials/chemicals 数值  
- Claims 引用 Wiki 句  
- DOE 硬边界自动采纳 L2 建议  
- 新建第二向量库或平行 `wiki_system` 目录 SSOT  
- 在 Theme Compiler 内重跑 OCR/markitdown  
- 完整人工编辑器（仍预留 `reviewed`）

可选远期（需单独评审）：离线「导出 MkDocs 包」——只读导出，不得回流为 SSOT。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 编造配方比例 | 表格先确定性填数；LLM 禁改数字；单测锁表 |
| 图片路径断裂 | ContextPack 只传已存在资产；写入前 `exists` 检查 |
| 与 system_overview 模板混淆 | `template` 字段分流；Hub 分类型展示 |
| 成本/耗时 | 默认旗标关；手动触发；骨架可无 LLM |
| 用户以为 Wiki=Claims 证据 | Reader Flag「LLM / 未审」+ Claims 路径不变 |

---

## 8. 建议排期

| 切片 | 估时 | 依赖 |
|------|------|------|
| P4.0 契约/旗标 | 0.5d | 无 |
| P4.1 ContextPack | 1–2d | campaign/DOE 读模型 |
| P4.2 骨架编译 | 1d | P4.1 |
| P4.3 LLM + API | 1–2d | P4.2、LLM key |
| P4.4 Hub 入口 | 1d | P4.3 |
| P4.5 测试文档 | 0.5–1d | 并行 |

合计约 **5–8 人日**。

---

## 9. 对你脚本的「最小改写」对照

| 原脚本 | 改为 |
|--------|------|
| `FormuMindWikiCompiler` | `compile_theme(template="project_tech", campaign_id=…)` |
| `fetch_formumind_data` | `build_project_theme_context` |
| `call_llm_compiler` | `_try_llm_project_narrative`（失败回落骨架） |
| `write_and_build_wiki` + `mkdocs build` | `wiki_store.upsert` + 前端 Reader 热读 |
| `docs/01_Technical_Themes/` | `data/wiki/themes/project-*.md` |

---

## 10. 拍板问题（实施前只需确认 3 项）

1. **触发入口**：仅 Hub「按项目生成」手动，还是 campaign 完成后可选自动？（建议：**仅手动**，与现 L2 一致）  
2. **首个垂直模板文案**：先固化「硅烷/转化膜/水性防腐」system 附加 prompt，还是通用 `project_tech`？（建议：**通用模板 + 可插拔 vertical addendum**）  
3. **导出 MkDocs**：本阶段是否连「离线导出」都不做？（建议：**不做**，维持 ADR）

确认后即可按 P4.0→P4.4 开工。
