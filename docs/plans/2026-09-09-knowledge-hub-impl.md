# Knowledge Hub 实施计划 — 资料运营台 + Wiki/图谱统一入口

状态：**范围已确认，待实施**（2026-09-09）  
性质：**非核心增强层**（服务检索→全文→配方/DOE 主闭环，不替代左栏检索工作台）  
开发约定：**直接在 `main` 迭代**（与 W0–W4 Wiki 一致）  
关联：[`2026-09-09-rag-llm-wiki-hybrid.md`](./2026-09-09-rag-llm-wiki-hybrid.md) · P3.2 [`2026-09-09-kb-ingest-evidence-fulltext.md`](./2026-09-09-kb-ingest-evidence-fulltext.md)

---

## 1. 产品确认（已锁定）

### 1.1 做什么

| 项 | 决策 |
|----|------|
| **入口** | 右栏「材料库」下方增加 **「知识库 · Knowledge Hub」** 按钮 |
| **壳** | 大弹窗；**顶部卡片式菜单**切换视图 |
| **卡片（首期）** | **资料** · **Wiki** · **图谱** · **文档生成（预留）** |
| **资料列表** | 宽表：来源、标题、URL、入库状态；行操作：**预览 · 打开 · 全文入库 · 删除** |
| **左栏复用** | 复用 Sources 已有能力（检索证据 + KB 文档 + ingest + 切块预览）；**新增**打开源、KB 级删除 |
| **Wiki** | 迁入现有只读 Wiki 浏览器（左栏 Wiki 小按钮可保留或改为打开 Hub-Wiki 页） |
| **Neo4j** | 弱卡片：状态探针 + 化合物/配方浏览；**不做**完整图可视化编辑器 |
| **文档生成** | **仅预留 Report**（类似 NotebookLM Create report）；Deck 作为 Report 子模板占位 |
| **不做** | Flashcards · Quiz · Mind Map **顶级菜单**；Wiki WYSIWYG；第二套 ingest 逻辑 |

### 1.2 左栏 vs Hub 分工

```text
左栏 Sources          → 边检索边选源、轻量操作（主工作台）
右栏 Knowledge Hub    → 资料治理、宽屏列表、Wiki/图谱/报告入口（运营台）
```

**禁止：** Hub 与左栏各写一套 ingest / eligibility / 状态机。

---

## 2. 信息架构

### 2.1 弹窗结构

```text
┌─ Knowledge Hub ─────────────────────────────────────────────┐
│ [资料] [Wiki] [图谱] [文档生成·预留]              [刷新] [×] │
├─────────────────────────────────────────────────────────────┤
│  （当前 Tab 内容区）                                          │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Tab 说明

| Tab | 首期深度 | 数据源 |
|-----|----------|--------|
| **资料** | **完整** | 会话 `sources[]` ∪ `GET /api/kb/sources` 合并去重 |
| **Wiki** | 只读列表+详情 | `GET /api/wiki/*` |
| **图谱** | 探针 + 列表 | `GET /api/kg/stats` · `/api/kg/neo4j/stats` · compounds 浏览 |
| **文档生成** | **占位 UI** | 无后端；模板卡 +「即将推出」 |

### 2.3 资料行统一模型（前端）

```typescript
interface HubMaterialRow {
  row_key: string;           // evidence.identifier | kb.source_id
  kind: "session" | "kb";    // 仅会话 vs 已持久化
  title: string;
  source: string;            // USPTO / surechembl / literature / upload …
  identifier: string;
  url?: string | null;       // url | oa_pdf_url | origin_url
  kb_status?: string;        // queued|fetching|indexing|indexed|skipped|failed
  source_id?: string | null; // KB UUID when indexed
  selected?: boolean;        // 会话勾选状态（仅 session 行）
}
```

**合并规则：**

1. 以 `identifier` / `origin_url` / patent alias 去重（复用 `patentIdAliases` / `idsMatch`）。  
2. 同一条既在会话又在 KB → 单行展示，`kind=kb` 优先，保留 `selected`。  
3. 排序：KB 文档在前（按 `created_at` 降序），未入库会话证据在后。

---

## 3. 资料 Tab — 行操作规格

| 操作 | 会话证据（未入库） | 已入库 KB 文档 | 实现要点 |
|------|-------------------|----------------|----------|
| **预览** | 有 `source_id` → `SourceDetailModal`；否则 snippet 抽屉 | `SourceDetailModal` | 复用现有 Modal |
| **打开** | `window.open(url \|\| oa_pdf_url \|\| url_alt)` | `origin_url` 或 Google Patents 构造 | 无 URL 则 disabled + tooltip |
| **全文入库** | `POST /api/kb/ingest-evidence` | 「再入库全文」同 API | 复用 `ingestEvidenceFulltext` 抽成 hook |
| **删除** | `removeSource(id)` — 移出会话 | **`DELETE /api/kb/sources/{id}`（新增）** | 二次确认；区分文案 |

### 3.1 KB 删除语义（新增后端）

**`DELETE /api/kb/sources/{source_id}`** — 级联清理（事务内 best-effort）：

| 层 | 动作 |
|----|------|
| `document_chunks` | `chunk_store.delete_for_source` |
| KG | `entity_store.delete_mentions_for_source` · `delete_links_for_source` |
| Wiki | 从各页 `source_ids` 移除；空页可 lint 标 `stale`（不自动删页） |
| `SourceDocument` | 删行 |
| 磁盘 | 不删 `data/wiki/*.md`（仅 SQLite 索引；可选后续 GC） |

**不做：** 删 Neo4j 节点（投影为 best-effort 副本）。  
**权限：** 与现有 API auth 一致；生产需确认 token。

### 3.2 左栏额外能力在 Hub 的映射

| 左栏能力 | Hub 资料 Tab |
|----------|--------------|
| 勾选用于 Chat | 复选框列（session 行） |
| 入库图谱 SureChEMBL | 行内「图谱」子按钮（surechembl 源） |
| 实施例草稿 | 有 `source_id` + eligibility 时显示 |
| KB ingest 进度 badge | 状态列 |

---

## 4. Wiki / 图谱 Tab

### 4.1 Wiki

- 迁入 `WikiBrowserModal` → `KnowledgeHubWikiPane`（列表 + Markdown 详情 + Flag 筛选）。  
- 左栏「Wiki」按钮改为 `openKnowledgeHub({ tab: "wiki" })`（可选 H1 后做）。  

### 4.2 图谱（弱）

**默认：** SQLite KG stats（`/api/kg/stats`）+ 实体计数。  
**Neo4j 已启用：** 追加 `/api/kg/neo4j/stats` + `GET /api/kg/neo4j/compounds?limit=` 简单表格。  
**不做：** Cytoscape / 力导向全屏编辑器（记入 W6+）。

---

## 5. 文档生成 Tab（预留）

### 5.1 首期 UI（无生成）

卡片网格（对标 NotebookLM **Create report**）：

| 模板卡 | 状态 |
|--------|------|
| Briefing Doc / 文献简报 | 占位 |
| 技术可行性评估 | 占位 |
| 配方对比纪要 | 占位 |
| 专利挖掘备忘 | 占位 |
| 演示文稿 Deck | 灰色子项「Report 之后」 |

点击 → 二级 Modal：提示词 + 来源范围（当前项目 KB 篇数）+ **「即将推出」** 按钮 disabled。

### 5.2 未来实现（H5+，本计划不排期）

- 后端 `POST /api/kb/generate-report`：RAG 多 chunk + Wiki 页 + 模板 prompt → Markdown/DOCX。  
- 引用强制 `[source_id]`；免责声明页脚。  
- **不做** Flashcards / Quiz；Mind Map 仅可作为 Report 附录图，不进顶级 Tab。

---

## 6. 切片与排期

| 切片 | 内容 | 估时 | DoD |
|------|------|------|-----|
| **H0** | 右栏入口 + `KnowledgeHubModal` 壳 + 卡片路由 | 0.5–1d | 四 Tab 可切换；资料/Wiki/图谱/文档生成占位可见 |
| **H1** | 资料 Tab：合并列表 + 预览/打开/入库 | 1.5–2d | 与左栏 ingest 行为一致；宽表可排序 |
| **H2** | `DELETE /api/kb/sources/{id}` + 删除 UI + 测试 | 1–1.5d | 删后 chunks/mentions 清空；Wiki source_ids 更新 |
| **H3** | Wiki pane 迁入；图谱弱 pane | 0.5–1d | Wiki 与现 API 一致；Neo4j 关时友好提示 |
| **H4** | 文档生成占位 Modal + 模板卡 | 0.5d | 无后端；产品可演示路线图 |
| **H5** | 共享 hooks 重构左栏（可选） | 1d | `useMaterialRowActions` 单处维护 |

**建议顺序：** H0 → H1 → H2 → H3 → H4；（H5 与 H1 并行或紧随其后）。

**总估：** 约 **4–6 人日**（不含 Report 真生成）。

---

## 7. 文件落点（预期）

| 层 | 路径 |
|----|------|
| 计划 | 本文 |
| 入口 | `frontend/src/components/ActionsPanel.tsx` — Hub 按钮（材料库下） |
| Hub 壳 | `frontend/src/components/knowledge-hub/KnowledgeHubModal.tsx` |
| Tab | `HubMaterialsPane.tsx` · `HubWikiPane.tsx` · `HubGraphPane.tsx` · `HubReportsPlaceholderPane.tsx` |
| 共享 | `frontend/src/hooks/useHubMaterialRows.ts` · `useMaterialRowActions.ts` |
| API 客户端 | `frontend/src/api.ts` — `deleteKbSource` · store slice |
| 后端删除 | `backend/app/api/kb.py` — `DELETE /sources/{id}` |
| 后端服务 | `backend/app/services/kb_delete.py` — 级联编排 |
| Store | `frontend/src/store/slices/uiSlice.ts` — `knowledgeHubOpen` · `knowledgeHubTab` |
| 测试 | `backend/tests/test_kb_source_delete.py` · `frontend/.../KnowledgeHub*.test.tsx` |

---

## 8. 测试计划

| 测试 | 断言 |
|------|------|
| `test_delete_kb_source_cascades_chunks` | chunks=0 |
| `test_delete_kb_source_cleans_kg_mentions` | mentions 删 |
| `test_delete_kb_source_wiki_source_ids` | wiki 页仍存但 source_ids 不含该 id |
| `test_delete_missing_source_404` | 404 |
| FE：资料合并去重 | 同 patent 会话+KB 一行 |
| FE：打开 disabled 无 URL | 按钮灰 |
| FE：删除确认 | 取消不删 |
| FE：Wiki Tab | 列表 200 |
| FE：文档生成 Tab | 模板卡渲染；Generate disabled |

---

## 9. 红线与非目标

1. **不**复制 ingest / kb_ingest 队列逻辑 — 只调现有 API。  
2. **不**做 Flashcards / Quiz / Mind Map 产品化。  
3. **不**在 Hub 内做检索主题输入（仍在左栏）。  
4. **不**把 NotebookLM 外源 Report 与 FormuMind KB Report 混为一个按钮。  
5. 删除必须 **确认对话框** + 区分「移出列表」vs「从知识库永久删除」。  
6. Hub 关闭不影响左栏状态；Hub 打开应读 store 最新 `sources` / `kbIngest`。

---

## 10. 风险

| 风险 | 缓解 |
|------|------|
| 双入口行为分叉 | H1 抽 shared hooks；左栏逐步薄化 |
| 删除误伤 Wiki/KG | 确认文案；只清 source_ids 不删整页 |
| 打开链接 404 / 付费墙 | 打开前 prefer `oa_pdf_url`；失败 toast |
| 列表过大 | 分页 `limit=100`；Hub 内搜索框（H1.1） |
| Report 期望过高 | 占位明确「研发草稿·需人工审核」 |

---

## 11. 成功指标

| 指标 | 目标 |
|------|------|
| 用户从 Hub 完成入库/预览/删除 | 无需回左栏找隐藏按钮 |
| 左栏与 Hub ingest 结果一致 | 0 回归 |
| Wiki 访问路径 | ≤2 次点击（右栏→Hub→Wiki） |
| Report 占位 | 利益相关方可对齐路线图，无「假可用」投诉 |

---

## 12. 状态

- [x] 产品范围确认（资料 + Wiki + 弱图谱 + Report 预留）  
- [x] 非目标确认（Flashcards/Quiz/Mind Map 顶级；Notebook 克隆）  
- [x] 详细实施计划成文  
- [ ] H0–H4 代码落地  
- [ ] 测试通过 · 已推 `main`  

---

## 13. 与 Wiki / 延后项关系

| 已有能力 | Hub 中的位置 |
|----------|--------------|
| W0–W4 Wiki | Wiki Tab |
| 左栏 Sources | 资料 Tab 数据源之一 |
| P3.2 ingest-evidence | 资料行「全文入库」 |
| Neo4j 薄投影 | 图谱 Tab 只读浏览 |
| Report 真生成 | **H5+** 独立切片，依赖 KB+Wiki 燃料充足后再开 |

**Recommend pitfall 降权 / BayBE 硬切 / Wiki 手改 / ELN 回流** 仍按 Wiki 计划延后；Hub 不阻塞这些项。
