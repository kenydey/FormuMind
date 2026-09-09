# RAG + LLM Wiki 混成升级评估与落地切片

状态：**架构评估 + 实施计划（待产品确认后分切片落地）**（2026-09-09）  
范围：在 **不破坏** 现有检索 / 问答 / 推荐 / DOE / ELN 的前提下，把 FormuMind 从「被动 RAG」升级为 **Raw 溯源 + LLM 编译 Wiki + 双轨检索**  
底座契合：磁盘 Markdown（拟新增 Wiki 页）· SQLite（已有）· Neo4j（可选同步，已有适配）

---

## 0. 一句话结论

| 问题 | 结论 |
|------|------|
| 有没有必要？ | **有必要**——硬科技配方场景需要「跨文献熔炼的约束与机理」，纯 chunk RAG 做不到稳定的避坑/边界喂 DOE |
| 要不要推倒重来？ | **不要**。现有 Raw→Parse→Chunk→Embed→KG 已覆盖 Wiki 三层里的 **输入层 + 半编译索引** |
| 正确形态？ | **RAG + LLM Wiki 混成**：Wiki 负责「编译记忆」；Chunk RAG / KG 负责「溯源与拓扑」；Query 双轨融合 |
| 最大风险？ | 再建一套平行 Markdown RAG → 双真相、双索引、功能分裂 |

**目标架构（保持现网契约）：**

```text
Raw (专利/论文/TDS/ELN)     ← 只读溯源（已有 SourceDocument + chunks）
        │
        ▼
LLM Compiler Agent（新）     ← 解引用 / 合并 / 打冲突旗（缺这一层）
        │
        ▼
Persistent Wiki（新）        ← materials/*.md · mechanisms/*.md · pitfalls/*.md
        │                      + SQLite 结构化字段 + KG 边（已有可复用）
        ▼
Query 双轨                   ← Wiki 凝练答 + Chunk/KG 证据锚（增强 chat/recommend/DOE）
        │
        ▼
DOE / BayBE / 推荐           ← 从 Wiki 抽 constraints（增强 factor_suggest / systems）
```

---

## 1. 与现网对照（已实现 vs 缺口）

### 1.1 三层数据流映射

| LLM Wiki 层 | FormuMind 现状 | 成熟度 | 说明 |
|-------------|----------------|--------|------|
| **Raw Input** | `SourceDocument` + 解析 Markdown + `document_chunks` + `origin_url`/`content_hash` | ★★★★★ | `ingestion` / `kb_ingest` / `parsing`；默认 `prune_source_fulltext` 后正文在 chunks |
| **Compiler** | 仅有 per-doc `SourceGuide`、chem/product 抽取、可选 KG 关系、实施例草稿 | ★☆☆☆☆ | **没有跨文档合并重写到规范页** |
| **Persistent Wiki** | 无人类可编辑的规范 Markdown 页树 | ☆☆☆☆☆ | 有 KG 实体 / `kb_products` / `FormulationVersion`，但 ≠ Wiki 页 |
| **Query** | Chat → chunks (+ 可选 KG)；Recommend → ColBERT；DOE → `aggregate_parameter_space` | ★★★★☆ | 强在 raw 检索，弱在「已熔炼结论」 |
| **Linting** | `kg/contradiction.py` 打分降权 | ★★☆☆☆ | 有矛盾信号，**不写回 Wiki Flag / 不驱动主编巡检** |

### 1.2 已可直接复用的「燃料」

| 能力 | 落点 | Wiki 用法 |
|------|------|-----------|
| 结构感知切块 + 向量 | `kb_index.index_source` | Wiki 页亦可 chunk/embed，或只对页摘要建索引 |
| 牌号/CAS/SMILES 注册 | `chem_extract` · `kb_products` · `materials` | Wiki 页 ID = `material:{norm_key}` / `chem:cas:…` |
| KG 实体与边 | `entity_store` · `kg/*` · 可选 Neo4j | 页 front-matter ↔ entity_id；边 = 拓扑 |
| Per-doc LLM 摘要 | `source_guide` | Compiler 的**单篇输入卡片**，不是终态 Wiki |
| 实施例/SCHEMBL 人审草稿 | `embodiment_drafts` · `surechembl_drafts` | 确认后写入 `formulations/*.md` + KG |
| DOE 参数聚合 | `kb_index.aggregate_parameter_space` / `doe_parameter_hints` | 升级为读 Wiki `constraints` 段 |
| 推荐接地 | `grounded_recommend` · `product_hints` · ColBERT | 增加 Wiki `systems/` / `pitfalls/` 检索通道 |
| 替代证据 | `substitution._kg_evidence` | Wiki 页「替代关系」段落 + KG `substitutes` |

### 1.3 明确缺口（必须新建，但要薄）

1. **Wiki 页模型**（磁盘 Markdown + SQLite 元数据行，双写）  
2. **Compiler 作业**（ingest 后异步：实体对齐 → 打开/创建页 → 合并段落 + `source_ids`）  
3. **双轨 Query**（Wiki hits + Chunk hits，引用仍回 Raw）  
4. **Lint 主编任务**（矛盾 Flag、孤立实体、缺链配方）  
5. **Constraints 导出 API**（供 DOE/BayBE，不改实验主路径契约）

---

## 2. 必要性评估（是否值得做）

### 做混成的收益（对你列的三大能力）

| 能力 | 现状痛点 | Wiki 增益 |
|------|----------|-----------|
| **问答/推理** | 每次从 PDF 切片拼答案，机理分散、易矛盾 | 问 `mechanisms/`、`pitfalls/` 得跨文献凝练结论，chunk 仅作脚注 |
| **配方推荐** | 依赖检索命中 + 产品 hint，缺「体系级避坑」 | Wiki `systems/{id}.md` 固化配比窗口与禁忌，推荐可引用 |
| **DOE** | `parameter_space` 来自各 SourceGuide 浅聚合 | Wiki 编译后的 **上下限 + 禁区** 直接喂 BayBE bounds |

### 不做 / 缓做的理由（架构师红线）

- 若 Compiler 质量不稳，Wiki 会变成「一本错得整齐的书」——必须 **人审 Flag + Raw 溯源**。  
- 全量替换 Chat 为 Wiki-only → 丢失证据粒度与法务溯源。  
- 与 P3.1b（表抽取质量）抢优先级时：**先保证 Raw 表配比可靠，再编译**（垃圾进、垃圾出）。

**产品建议：**  
**值得做**，但定位为 **增强层（feature-flag 默认关）**；Raw RAG 永远保留为权威溯源。优先级排在 P3.1b F0–F2 稳定之后，或与之并行但 **Compiler 只消费 eligible 全文**。

---

## 3. 目标架构（与现模块契合、零破坏）

### 3.1 存储契约（避免双真相）

```text
data/wiki/
  materials/{norm_key}.md      # 牌号/树脂/固化剂
  chemicals/{cas_or_hash}.md   # 规范化学物
  systems/{system_id}.md       # 配方体系（对接 formulation_systems）
  mechanisms/{slug}.md
  pitfalls/{slug}.md
  patents/{compact_pub}.md     # 可选：专利卡片（链回 SourceDocument）
  _index.jsonl                 # 可选：编译清单

SQLite wiki_pages（新表，薄）
  id, path, kind, entity_id, title, content_hash,
  source_ids JSON, flags JSON, updated_at, revision

document_chunks / kb_entities / kb_products     ← 不变，仍是 Raw/索引权威
Neo4j                                          ← 可选：从 wiki_pages + entity_links 投影
```

**原则：**

- Raw chunk **永不被 Wiki 覆盖删除**。  
- Wiki 每段 claim 必须带 `source_ids` / chunk 锚（能点回原 PDF）。  
- 结构化硬字段（CAS、wt% 上下限）**以 SQLite 为准**；Markdown 是可读投影。  
- 人类可手改 `.md` → 下次 compile 走 **三路合并策略**（见 4.2），不静默覆盖人工段。

### 3.2 对现有 API 的影响（兼容）

| 模块 | 变化 | 破坏性 |
|------|------|--------|
| `POST /api/chat` | `_augment_with_kb` 增加 Wiki 通道（flag） | 无：默认关闭则行为与现网一致 |
| Recommend / research | 检索列表并入 wiki hits | 无：flag |
| DOE hints | `doe_parameter_hints` 优先读 Wiki constraints | 无：回退 SourceGuide 聚合 |
| Ingest / kb_ingest | 成功后 **enqueue compile job** | 无：异步，失败不影响入库 |
| KG / Neo4j | compile 时 upsert 边 | 无：沿用 entity_store |
| ELN / Datalab | 实验回流写 SQLite → 可选 compile 入 `systems/` 或 `runs/` | 后置切片 |

---

## 4. 生命周期落地（对应你描述的 Ingest / Query / Lint）

### 4.1 Ingest（摄入与重写）— 挂在现有管道末尾

```text
index_source / link_source 成功
    → task: wiki_compile_source(source_id)
        1. 读 chunks + source_guide + chem/products + (可选) embodiment confirm
        2. 实体对齐（norm_key / CAS / patent compact）
        3. 对每个实体：load wiki page or create stub
        4. LLM：merge 新证据段落（带 source_id），去重近义句
        5. 写 .md + wiki_pages 行 + 可选 embed wiki 摘要 chunk（meta.wiki=true）
```

**禁止：** 在 uvicorn 请求线程同步跑长 compile；必须 Celery/后台任务（现有 `dispatch_kb_ingest` 同模式）。

### 4.2 Query（双轨融合）

```text
用户问题
  ├─ Track A: search_wiki(q) → 凝练页段落（高优先级上下文）
  ├─ Track B: search_chunks / kg.retrieve → 原始证据
  └─ LLM 作答：先 Wiki 综合，再强制引用 Raw Evidence（claims check 仍跑）
```

实现落点：扩展 `api/chat.py` `_augment_with_kb` 与 `kg/retrieval.py`，**不**新建聊天服务。

### 4.3 Linting（主编体检）

```text
wiki_lint_all / wiki_lint_after_batch
  - 矛盾：复用 kg/contradiction + LLM 核对 → page.flags += conflict
  - 孤立：entity 无配方边 / 页无 source_ids
  - 过期：source 已删但页仍引用 → flag stale
```

UI：资料面板或独立「Wiki 健康」只读列表即可（第一期不必重编辑器）。

### 4.4 喂 DOE / 推荐（燃料舱）

```text
GET /api/wiki/constraints?system_id=epoxy_amine
  → { variables: [...], bounds: [...], forbidden: [...], sources: [...] }

factor_suggest / baybe_space_builder：
  if wiki_constraints_enabled: merge bounds
  else: 现有 SourceGuide 聚合（回退）
```

实验回流：`workbench` 写入测量后 → `wiki_compile_run(experiment_id)` 把成功/失败点追加到 `systems/` 或 `pitfalls/`（标注 `private_lab=true`）。

---

## 5. 实施切片（高效、少冗余）

> 总原则：**一个 Compile 服务 + 一张 wiki_pages 表 + 一套 Markdown 约定**；禁止第二套 embedding 业务线（Wiki 摘要可写入现有 `document_chunks`，`source_kind=wiki`）。

### Slice W0 — 决策与旗标（0.5 天）

- [ ] `FORMUMIND_WIKI_ENABLED`（默认 **false**）  
- [ ] `FORMUMIND_WIKI_COMPILE_ON_INGEST`  
- [ ] `FORMUMIND_WIKI_CHAT_BLEND` / `FORMUMIND_WIKI_DOE_CONSTRAINTS`  
- [ ] 写入 `env_flags.py` + 计划本文确认

### Slice W1 — Wiki 页存储最小内核（1–2 天）

- [ ] `data/wiki/` 目录约定 + front-matter schema  
- [ ] SQLite `wiki_pages` + `WikiStore`  
- [ ] API：`GET /api/wiki/pages` · `GET /api/wiki/pages/{id}`（只读）  
- [ ] 单测：创建/更新/content_hash 幂等  

**验收：** 手写一页 `materials/e-51.md` 可列出并读取；不影响 chat。

### Slice W2 — Compiler v1（材料页合并）（2–3 天）

- [ ] `wiki_compile.compile_source(source_id)`  
- [ ] 仅处理：`kb_products` / chem 实体 → `materials/` · `chemicals/`  
- [ ] 合并策略：追加「证据段」+ 更新 SQLite 属性；冲突 → `flags`  
- [ ] ingest/kb_ingest 成功后 `dispatch_wiki_compile`（flag 控制）  
- [ ] **不**在此切片改 chat

**验收：** 两篇专利同牌号入库后，一页 `materials/{key}.md` 含两个 `source_ids`。

### Slice W3 — Chat 双轨（1–2 天）

- [ ] `_augment_with_kb`：Wiki top-k + chunk top-k  
- [ ] Prompt：区分「编译结论」与「原始摘录」  
- [ ] `chat_claims` 仍只依据 Raw Evidence（防 Wiki 幻觉逃逸）  

**验收：** flag 关 = 旧行为；flag 开 = 答案可引用 Wiki 标题且 citations 仍有 chunk。

### Slice W4 — DOE / 推荐约束导出（1–2 天）

- [ ] 从 `systems/*.md` front-matter / SQLite 读 bounds  
- [ ] 接入 `doe_parameter_hints` / `factor_suggest`（回退链保留）  
- [ ] Recommend soft-bonus：命中 pitfalls 则降权违规配方  

**验收：** 有 Wiki 禁区「催化剂 >1.5%」时，BayBE/提示空间被裁切；无 Wiki 时与现网一致。

### Slice W5 — Lint 主编（1–2 天）

- [ ] 异步 `wiki_lint`：矛盾 / 孤立 / 陈旧  
- [ ] UI 只读 Flag 列表  

### Slice W6 — 机理/避坑页 + 实验回流（后续）

- [ ] `mechanisms/` · `pitfalls/` 专题编译  
- [ ] ELN 测量回流 compile  
- [ ] Neo4j 投影（可选）  

---

## 6. 非目标（防膨胀）

- 用 Wiki **替换** `document_chunks` 或关掉 ColBERT  
- 在 Compiler 里重做 OCR/PDF 解析（继续用 `parsing` / P3.1b）  
- 自动把 Wiki 结论写入生产配方池  
- 第一期做完整 Wiki 可视化编辑器（Git 式手改 + API 读写即可）  
- 为 Wiki 单独引入第二向量数据库  

---

## 7. 与近期路线的排序建议

```text
P3.1b F0–F2（表/噪音）     ← 提高 Raw 燃料质量（进行中/刚做）
    │
P3.2 入库全文漏斗（若未合入）← 保证有全文可编译
    │
W0–W2 Wiki 内核 + 材料编译  ← 最小可用「第二大脑」
    │
W3 Chat 双轨                 ← 用户可感知问答增强
    │
W4 DOE/推荐约束              ← 闭环燃料舱
    │
W5–W6 Lint + 机理/实验回流
```

---

## 8. 风险与治理

| 风险 | 缓解 |
|------|------|
| 编译幻觉污染 Wiki | 强制 `source_ids`；Chat claims 只认 Raw；Flag 人审 |
| 与手改冲突 | front-matter `human_locked` 段；compile 只追加 `## Evidence` |
| 成本/延迟 | 默认关；按 source 增量编译；批量限流 |
| 代码冗余 | 单模块 `services/wiki/`（store/compile/lint/retrieve）；禁止复制 `kb_index` |
| Neo4j 双写漂移 | SQLite KG 仍权威；Neo4j 仅投影 |

---

## 9. 成功指标

| 指标 | 目标 |
|------|------|
| Wiki 页覆盖的 `kb_products` 比例 | 逐步 ↑ |
| Chat：Wiki blend 开启后「无证据断言」率 | 不高于基线（claims 检查） |
| DOE：来自 Wiki 的 bound 采用次数 | 可观测 |
| flag 关时全量回归 | chat / recommend / doe / ingest **零行为变化** |

---

## 10. 文件落点（预期）

| 层 | 路径 |
|----|------|
| 计划 | 本文 |
| Store / API | `backend/app/db/wiki_store.py` · `backend/app/api/wiki.py` |
| Compiler / Lint / Retrieve | `backend/app/services/wiki/{compile,lint,retrieve,schema}.py` |
| 挂接 | `kb_index.index_source` 末尾 · `kb_ingest` · `api/chat.py` · `factor_suggest.py` |
| 旗标 | `config.py` · `env_flags.py` |
| 前端（后期） | Wiki 只读浏览 / Flag 列表（可先 API-only） |

---

## 11. 状态

- [x] 与现网架构对照评估  
- [x] 混成方案与非目标成文  
- [x] 切片 W0–W6 排期建议  
- [ ] 产品确认：默认关混成、W1–W2 为第一批  
- [ ] 实施 W0–W2  

**架构师裁决：** FormuMind **应该**升级为 RAG+LLM Wiki 混成，但 **Wiki 是编译层不是新底座**；SQLite chunks + KG 继续做溯源与拓扑，Markdown Wiki 做人读记忆与约束燃料。这样问答、推荐、DOE 都能增强，且现有功能在旗标关闭时完全不受影响。
