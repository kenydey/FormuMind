# 实施计划：可视化 Knowledge Graph（对标 llm_wiki 交互，FormuMind 自研）

> 状态：**P1 已实现（待合入）**（2026-09-20）· P0 已合入 `#124`  
> 动机：希望拥有接近 `nashsu/llm_wiki` 的 **可交互图谱前端 + 图构建后端**  
> 对照：`vendor/llm_wiki`（GPL，只读）· 前序 [`2026-09-20-llm-wiki-borrow-ranked-plans.md`](./2026-09-20-llm-wiki-borrow-ranked-plans.md) §S6  
> 约束：**不复制 GPL 源码**；**不混**「Wiki 页链接图」与「配方/材料 KG」；不上 LanceDB；旗标灰度；不默认 LLM

---

## 0. 结论摘要（先读）

| 问题 | 答案 |
|------|------|
| llm_wiki 的「图谱」是什么？ | **Wiki 页面 wikilink 图**（Sigma.js + Graphology + Louvain），不是化学材料图谱 |
| FormuMind 今天有什么？ | **配方 KG**（SQLite ± Neo4j）+ Hub「图谱」**弱探针（统计/表格）**；**无**页图可视化 |
| 要「像 llm_wiki」该做什么？ | **新建 Wiki 页图产品线**（后端构图 API + Hub 画布），自研重写交互 |
| 配方 KG 要不要一起做？ | **可选二期**：同一画布壳，数据源切换「页图 / 材料关系」；一期先页图 |

**推荐主路径：P0 Wiki 页图可视化 → P1 缺口洞察+行动 → P2 材料 KG 画布（复用壳）。**

---

## 1. 两边对象对齐（避免做错图）

### 1.1 llm_wiki（要对标的体验）

| 层 | 事实 |
|----|------|
| 渲染 | Sigma.js + Graphology + ForceAtlas2（大图 Worker） |
| 节点 | 每个 `wiki/**/*.md`：`id/label/type/path/linkCount/community` |
| 边 | 正文 `[[wikilink]]`；权重 = 四信号 relevance（非化学边） |
| 社区 | Louvain；着色 type / community |
| UX | 过滤孤立/结构页、搜索、悬停高亮邻居、Surprising / Gaps 侧栏、缺口→Deep Research |
| 计算位置 | **前端 TS 读盘构图**为主；Rust API 仅无权重薄图 |

### 1.2 FormuMind（现网）

| 图 | 现状 | 入口 |
|----|------|------|
| **材料/配方 KG** | SQLite entities + links；可选 Neo4j 镜像 | Hub「图谱」=`HubGraphPane` 统计表；`KgRelationPanel` 列表 |
| **Wiki→Neo4j** | 薄投影 entity→Compound（`wiki_neo4j_project`） | **无** wikilink 边 |
| **Wiki 页邻接** | 仅 lint `detect_orphans` 扫入链 | Hub Wiki Flag，**无画布** |

### 1.3 决策锁定

1. **产品名分流**  
   - Hub Tab 文案建议：现「图谱」保留给 **材料 KG**；新增子页签或模式切换 **「Wiki 链接图」**（或独立 Tab「页图」）。  
2. **一期只做 Wiki 页图**（最像 llm_wiki）。  
3. **禁止**把 Louvain 社区写进 Neo4j / 当 DOE 硬约束。  
4. **算法与 UI 自研/MIT 依赖**（Sigma/Graphology 或 Cytoscape.js / @antv/g6 — 选型见 §4）；**禁止**拷贝 `vendor/llm_wiki/src/lib/wiki-graph*.ts`。

---

## 2. 目标体验（验收口径）

用户打开 Knowledge Hub → **Wiki 链接图**：

1. 看到节点（实体/概念/theme…）与 `[[wikilink]]` 边，可拖拽/缩放/点选。  
2. 可按 kind、度数、是否孤立过滤；可搜索标题/path。  
3. 点节点 → 打开 Wiki Reader（复用现有 detail）。  
4. （P1）侧栏「缺口」：孤立页 / 稀疏团 / 断链 → 动作：打开页 / 跑 Lint / 刷新卷宗（有 project 时）。  
5. （P2 可选）同一壳切换「材料关系」：substitutes / measured 等，数据来自 `/api/kg/*`。

非目标（一期）：

- 四信号完整复刻（可用简单权重：双向链接 ×2 + 一度共享邻居）  
- Deep Research 自动入队（可只留 CTA 文案）  
- 持久化图快照表（可内存/请求时构图）  
- 移植 Tauri / Rust agent 图搜索  

---

## 3. 架构（FormuMind 适配）

```text
wiki_pages + data/wiki/*.md
        │
        ▼
POST/GET /api/wiki/graph          ← 后端构图（权威，可测）
        │  { nodes[], edges[], meta }
        ▼
HubWikiGraphPane (canvas)         ← Sigma/G6 自研壳
        │  click node
        ▼
现有 Wiki Reader / open page
```

**为何后端构图（相对 llm_wiki 前端读盘）？**

- FormuMind Wiki 在服务端 SSOT（`wiki_pages`），浏览器无直接盘符。  
- 便于 pytest 锁边集合；与 lint 共用 wikilink 解析。  
- 前端只做布局与交互，符合现网 Hub 模式。

---

## 4. 技术选型

| 选项 | 建议 |
|------|------|
| **A. Sigma + Graphology**（最贴 llm_wiki） | P1 可升级；包体与 WebGL/jsdom 成本更高 |
| **A′. SVG 力导向（自研）** | **P0 已采用**：零新依赖、Hub 暗色易适配、Vitest 可点选；行为对标可拖/缩放/点选，非 GPL |
| B. @antv/G6 | 国内文档好；API 面大，一期够用但包体更大 |
| C. Cytoscape.js | 稳定；社区检测要自接 |
| D. 纯 SVG/Canvas 从零大图引擎 | 否决（工期）；P0 的轻量力导向 ≠ 完整引擎 |

依赖：仅 npm **MIT/Apache** 包；不 vendoring llm_wiki。

Louvain：用 `graphology-communities-louvain`（检查许可）或一期 **不做社区**，P1 再加。

---

## 5. 后端契约（P0）

### 5.1 端点

```http
GET /api/wiki/graph?limit=500&kinds=entity,concept,theme&include_orphan=1&project_id=
```

或 `POST` body 过滤（与 probe 风格一致亦可）。

**旗标：** `wiki_enabled`；建议新旗标 `wiki_page_graph_enabled`（默认 **false**）灰度。

### 5.2 响应（建议）

```json
{
  "ok": true,
  "nodes": [
    {
      "id": "materials/foo.md",
      "path": "materials/foo.md",
      "label": "Foo",
      "kind": "material",
      "flags": ["unreviewed"],
      "degree": 3
    }
  ],
  "edges": [
    { "source": "materials/foo.md", "target": "systems/bar.md", "weight": 1.0 }
  ],
  "meta": {
    "node_count": 120,
    "edge_count": 340,
    "truncated": false,
    "elapsed_ms": 45
  }
}
```

### 5.3 构图规则（锁定）

1. 源：`WikiStore` 列表 + `read_markdown`（或 DB 已存 body）。  
2. 解析 `[[wikilink]]`（复用/抽出与 `lint.detect_orphans` / Reader 同一套 normalize）。  
3. 跳过：空页；可选跳过 `reports/`、过大 `themes/project-*` 正文边（可配置）。  
4. 未解析目标：记 `broken` 计数进 meta，**不造幽灵节点**（或造虚节点带 `missing` flag — MVP 选前者）。  
5. `weight` MVP = 1.0；P1 = `1 + shared_neighbors` 归一化。  
6. `limit`：按度数优先截断节点，避免一次拉全库炸浏览器。

### 5.4 测试

- 三页 A→B、B→C 金样：边集合稳定。  
- 断链不产生 target 节点。  
- 旗标关 → 409。  
- 与 `detect_orphans`：入度为 0 的非结构页 ⊆ 图中 degree_in=0（允许结构页策略差异，文档写明）。

**落点文件（预计）：**

- `backend/app/services/wiki/page_graph.py`（新建）  
- `backend/app/api/wiki.py` 注册 route  
- `backend/tests/test_wiki_page_graph.py`

---

## 6. 前端契约（P0）

### 6.1 入口

- `KnowledgeHubModal`：在 **Wiki** Tab 内加子切换 `列表 | 链接图`，**或** 图谱 Tab 内加模式 `材料KG | Wiki链接图`。  
  **推荐：** Wiki Tab 子切换（页图属于 Wiki 导航）；材料画布仍放图谱 Tab（P2）。

### 6.2 组件

| 组件 | 职责 |
|------|------|
| `HubWikiGraphPane.tsx` | 拉 `/api/wiki/graph`、工具条、挂载画布 |
| `WikiPageGraphCanvas.tsx` | Sigma/G6 封装：布局、选中、缩放 |
| `wikiPageGraph.ts`（utils） | 过滤/搜索纯函数（易单测） |

### 6.3 UX MVP

- 工具条：刷新、搜索、隐藏孤立、按 kind 勾选。  
- 点击节点 → `setDetail(path)` 或打开现有 Reader 抽屉。  
- 空态：Wiki 未开 / 旗标关 / 无链接 → 文案引导 Lint。  
- 加载：`elapsed_ms` + 节点边计数。

### 6.4 测试

- mock API 渲染 N 节点；点击回调 path。  
- 过滤孤立后节点数减少。

---

## 7. 分期实施

### P0 — Wiki 页图 MVP（约 3–5 人日）★★★★★

| # | 任务 | 产出 |
|---|------|------|
| 1 | `page_graph.py` + GET API + 旗标 | 可 curl 的 JSON 图 |
| 2 | 单测金样 + 旗标 409 | CI 绿 |
| 3 | `HubWikiGraphPane` + Canvas（Sigma 或 G6） | Hub 可看可点 |
| 4 | 过滤/搜索/打开 Reader | 手测清单 |
| 5 | 本方案状态 → 实现中/已实现；手测补 W-Graph 节 | 文档 |

**验收：** 有内链的样例库打开页图，点击跳到对应 Wiki 页。

### P1 — 洞察与行动（约 2–3 人日）★★★★

| # | 任务 | 状态 |
|---|------|------|
| 1 | 侧栏：孤立节点、断链样例、弱连通分量计数 | **本 PR** |
| 2 | 动作：跑 Lint / 打开页 /（有 project）刷新卷宗 | **本 PR** |
| 3 | 边权：共享邻居加权；可选 Louvain 着色 | 延后（非验收阻塞） |
| 4 | `project_id` 邻域收紧（仅 dossier 链出） | P0 已有 project scope；进一步收紧延后 |

**验收：** 孤立页列表与 lint orphan 大体一致；一点动作有响应。

### P2 — 材料 KG 画布（复用壳）（约 3–5 人日）★★★

| # | 任务 |
|---|------|
| 1 | `GET /api/kg/graph?relation_types=substitutes,measured_*&limit=`（从现有 links 投影） |
| 2 | `HubGraphPane` 升级：表视图 | 画布视图 |
| 3 | 点实体 → 打开 `KgRelationPanel` / resolve |
| 4 | 明确 UI 标签「材料关系（配方 KG）」vs「Wiki 链接图」 |

**验收：** 关 Neo4j 仍可用 SQLite KG 画布；开 Neo4j 不强制。

### P3 — 明确延后 / 不做

- 复刻 llm_wiki 四信号 + surprise 文案全套  
- 图写入 Neo4j 当 SSOT  
- GPL 代码移植、Obsidian 双开、前端读本地 vault  
- 用页图社区驱动 DOE/Claims  

---

## 8. 旗标与配置

| 环境变量 / attr | 默认 | 含义 |
|-----------------|------|------|
| `FORMUMIND_WIKI_PAGE_GRAPH_ENABLED` | `false` | 开放 `/api/wiki/graph` + Hub 页图入口 |
| 依赖 | `wiki_enabled=true` | 否则 409 |
| （P2）复用 | `kg_enabled` | 材料画布 |

EnvFlagsPanel：挂在 kb 分类，hint「Hub Wiki · 链接图」。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 用户以为页图 = 配方 KG | Tab/模式醒目标签；空态说明差异 |
| 大库构图慢/卡 UI | `limit`+按度数截断；后端超时；Worker 布局 |
| wikilink 解析与 Reader 不一致 | **抽出共享 normalize**，lint/graph/reader 共用 |
| 包体变大 | 画布路由 `React.lazy` |
| 许可 | 只用 MIT 依赖；评审对照 llm_wiki 行为不拷代码 |

---

## 10. 工作量与依赖

| 阶段 | 人日 | 前置 |
|------|------|------|
| P0 | 3–5 | Wiki 已开；Hub 壳稳定 |
| P1 | 2–3 | P0 |
| P2 | 3–5 | `kg_enabled` 数据够用 |

**建议开刀指令：**  
「开独立方案 P0：Wiki 页图可视化（API + Hub 画布）」

---

## 11. 文件清单（P0 预计）

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-20-wiki-page-graph-viz.md` | 本方案 |
| `backend/app/services/wiki/page_graph.py` | 新建构图 |
| `backend/app/api/wiki.py` | `GET /graph` |
| `backend/app/services/env_flags.py` / `config.py` | 旗标 |
| `backend/tests/test_wiki_page_graph.py` | 金样 |
| `frontend/src/api.ts` | `getWikiPageGraph` |
| `frontend/src/components/knowledge-hub/HubWikiGraphPane.tsx` | 新建 |
| `frontend/src/components/knowledge-hub/WikiPageGraphCanvas.tsx` | 新建 |
| `KnowledgeHubModal` / `HubWikiPane` | 入口切换 |
| 手测清单 | 补 W-Graph |

---

## 12. 与「核心功能」的匹配说明

| 核心能力 | 页图如何增强 |
|----------|--------------|
| Hub Wiki / 卷宗 | 发现孤岛、断链，服务叙述与 Report 质量 |
| Lint / 灰度运维 | 可视化 orphan，少靠表格 |
| Claims / DOE | **只读导航**；图不进硬约束（延续 ADR） |
| 材料推荐 KG | **P2** 另画布；不在一期混进页图 |

---

## 附录 A. llm_wiki 对照路径（只读）

```text
vendor/llm_wiki/src/components/graph/graph-view.tsx
vendor/llm_wiki/src/lib/wiki-graph.ts
vendor/llm_wiki/src/lib/graph-relevance.ts
vendor/llm_wiki/src/lib/graph-insights.ts
```

## 附录 B. FormuMind 现网路径

```text
frontend/src/components/knowledge-hub/HubGraphPane.tsx   # 材料 KG 弱探针
backend/app/api/kg.py / kg_neo4j.py
backend/app/services/wiki/lint.py                       # orphan 扫描可复用
frontend/src/wiki/wikilinks.ts
```
