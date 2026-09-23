# STORM 风格长篇技术报告编排 — 设计评审稿（FormuMind Wiki）

> 状态：**实施中（P0–P2 MVP）** · 2026-09-23  
> 对照：Stanford STORM（Pre-writing → Writing → Polish）；**禁止**整仓移植 GPL/`storm` 源码，仅借范式自研  
> 约束：不破坏现有卷宗同步 Report / Wiki CRUD；旗标默认关；Claims 只 Raw；不默认 LLM 洗 L1  
> 前序：[`2026-09-22-grayscale-kg-maintrack.md`](./2026-09-22-grayscale-kg-maintrack.md) · [`2026-09-20-llm-wiki-borrow-ranked-plans.md`](./2026-09-20-llm-wiki-borrow-ranked-plans.md)

**MVP 落地（本 PR）：** `storm_schema` / `storm_outline` / `storm_draft` / `storm_polish` / `storm_orchestrator`；旗标 `wiki_storm_report_enabled`（默认 false）；Celery `formumind.wiki_storm_report` + `POST /api/wiki/storm/report`（202 + SSE）；确定性离线路径；同步 `POST /dossier/report` 零改动。

---

## 0. 结论摘要（先读）

| 问题 | 答案 |
|------|------|
| 今天长报告怎么生成？ | **同步** `POST /api/wiki/dossier/report` → DossierPack 切片表 → 可选执行摘要润色 → 落 `reports/project-*.md` |
| Agents（supervisor/chemist/inspector）参与报告吗？ | **否**。它们只挂配方可行性门（`feasibility.check_formulation`） |
| Celery chord/chain 现成吗？ | **无**。复用 `deep_research` 模式：单任务 + `ThinkingTracker` + SSE |
| 能否直接改 `generate_report`？ | **不建议**。保留同步短路径；新增 **高级编排** `wiki_storm_report`（异步） |
| STORM 与卷宗关系？ | Pack/卷宗 = **确定性证据底座**；STORM = **在底座之上的长文合成**（L2 draft，`draft_not_claims`） |

**推荐落地切片：** Schema → `report_storm` 服务 → Celery 任务 + SSE → 新 API；旧 `generate_report` 零回归。

---

## 1. 当前工作流断点图

```text
┌─ 现网「短报告」路径（保留）──────────────────────────────────────┐
│ project_id                                                         │
│   → ensure_project_dossier (optional)                              │
│   → get_dossier_pack / build_project_dossier_pack                  │
│   → render_report_markdown (template slices → MD tables)           │
│   → [_try_llm_polish] 仅 executive_summary（旗标 wiki_dossier_llm） │
│   → upsert reports/project-{id}-{tpl}.md                           │
│   → export md|docx|pdf|pptx                                        │
│ API: POST /api/wiki/dossier/report | /export  （同步，无 task_id）   │
└────────────────────────────────────────────────────────────────────┘

┌─ 相邻但未接通的能力（断点）──────────────────────────────────────┐
│ compile.py          L1 实体页编译（≠ 长文）                        │
│ theme.py            L2 system theme（≠ project report STORM）      │
│ dossier_narrative   卷宗节叙述两步（≠ 多章长文）                    │
│ agents/*            配方 Chemist/Inspector（≠ 报告角色）           │
│ citation_binder.py  [^n] 绑定（仅测试引用，生产报告未用）           │
│ research_graph      CRAG deep_research（有 SSE，≠ 报告）           │
│ wiki_compile task   KB 入库后 L1 compile（≠ 报告）                 │
│ resources/rules/*.toml  工艺规则（酸稳定性等，Inspector 未读 TOML）│
└────────────────────────────────────────────────────────────────────┘
```

### 1.1 关键符号（现网）

| 模块 | 符号 | 说明 |
|------|------|------|
| `services/wiki/report.py` | `generate_report`, `render_report_markdown`, `_try_llm_polish` | 同步模板报告 |
| `services/wiki/dossier_pack.py` | `build_project_dossier_pack` | S1–S8 证据底座 |
| `api/wiki.py` | `POST /dossier/report`, `/export` | 无异步 |
| `agents/supervisor.py` | `InitializeAgent.review` | 配方门，非 Wiki |
| `worker/tasks.py` | `run_deep_research_task` + `ThinkingTracker` | **应复用的进度范式** |
| `services/citation_binder.py` | `bind_citations`, `build_citation_prompt` | STORM Polish 首个生产调用方 |

### 1.2 与 STORM 的缺口

| STORM 阶段 | FormuMind 今天 |
|------------|----------------|
| Perspective-guided Q&A | 无 |
| 动态大纲 `ReportOutline` | 无（硬编码 `REPORT_TEMPLATES`） |
| 分章检索 + 滑动摘要 | 无（一次读满 Pack） |
| 并行分章写作 | 无（同步 HTTP） |
| 缝合 + 强引用校验 | 仅列 `source_ids`；无 `citation_binder` |
| 进度事件 | 无 |

---

## 2. 目标数据流（STORM × FormuMind）

```text
POST /api/wiki/storm/report  { project_id, topic?, perspectives?, max_sections?, use_llm }
        │
        ▼ 202 Accepted { task_id, status_url, stream_url }
        │
        ├─ Stage generating_outline ───────────────────────────────
        │    pack = get_dossier_pack(project_id)
        │    perspectives = LLM(3–5 角色) | 默认角色表
        │    per-role: rag/hybrid + optional Neo4j focal entities
        │    outline: ReportOutline  (Pydantic + tenacity retry)
        │    persist sidecar: reports/project-{id}-storm.outline.json
        │
        ├─ Stage drafting_section_{i} ─────────────────────────────
        │    for each SectionSpec (chain by dependency; sibling parallel optional):
        │      ctx = { outline tree, prev SectionDraft.summary, retrieval_queries }
        │      hits = rag / wiki retrieve / kg (scoped)
        │      draft = LLM → SectionDraft (markdown + used_citations + summary)
        │      publish_progress(stage=drafting_section_N, progress=…)
        │
        ├─ Stage stitching ────────────────────────────────────────
        │    concat sections + transition polish (LLM optional, flag)
        │
        ├─ Stage linting ──────────────────────────────────────────
        │    citation_binder.bind_citations / strip unbound [^n]
        │    optional: chemist-style soft rules + resources/rules hooks
        │    wiki lint (structure) soft
        │
        └─ Persist reports/project-{id}-storm.md
             flags: unreviewed, report, draft, storm
             disclaimer: draft_not_claims
             NOT Claims / NOT DOE bounds
```

**信任边界（硬）：**

- STORM 产物 = **L2 draft**；Claims evidence 过滤 `wiki:`；DOE 不读 `reports/` / `queries/`。
- Pack 数值表仍为 L1 确定性；STORM 叙述不得改写 S1–S6 表内数字（可引用「见表」）。

---

## 3. Schema 契约（建议放 `domain/schemas.py` 或 `services/wiki/storm_schema.py`）

> 推荐 **独立** `storm_schema.py` 再从 `domain` re-export，避免 `schemas.py` 继续膨胀；评审二选一。

```python
class SectionSpec(BaseModel):
    section_id: str
    title: str
    level: Literal[1, 2] = 1
    core_intent: str = ""
    target_word_count: int = 800
    focal_entities: list[str] = Field(default_factory=list)  # CAS / mat: / wiki path
    retrieval_queries: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)  # section_ids；空=可并行

class ReportOutline(BaseModel):
    schema_version: int = 1
    project_id: str
    topic: str
    global_summary_goal: str = ""
    estimated_total_words: int = 0
    perspectives: list[str] = Field(default_factory=list)
    sections: list[SectionSpec] = Field(default_factory=list)

class SectionDraft(BaseModel):
    section_id: str
    content_markdown: str
    used_citations: list[str] = Field(default_factory=list)  # source_id / kg node / doi
    summary: str = ""  # 150–200 字滑动窗
    word_count: int = 0
    model: str = ""

class StormReportState(BaseModel):
    """任务侧车状态（Redis/磁盘 JSON）。"""
    schema_version: int = 1
    project_id: str
    task_id: str
    stage: str  # generating_outline | drafting_section_N | stitching | linting | done | failed
    outline: ReportOutline | None = None
    drafts: dict[str, SectionDraft] = Field(default_factory=dict)
    final_path: str | None = None
    error: str | None = None
```

LLM 输出一律 `complete_structured(..., model=ReportOutline|SectionDraft)` + **Tenacity** 重试（JSON 校验失败）。

---

## 4. 模块改造方案（文件级）

### 4.1 新建

| 文件 | 职责 |
|------|------|
| `services/wiki/storm_schema.py` | 上节 Pydantic 模型 |
| `services/wiki/storm_outline.py` | Phase 1：角色视角 + 检索汇聚 + `ReportOutline` |
| `services/wiki/storm_draft.py` | Phase 2：单章 Prompt 组装 + 滑动 summary + 分章 LLM |
| `services/wiki/storm_polish.py` | Phase 3：缝合 + `citation_binder` + soft lint |
| `services/wiki/storm_orchestrator.py` | 编排状态机；供 Celery 调用 |
| `tests/test_wiki_storm_outline.py` 等 | 结构化假 LLM + 隔离 Claims/DOE |

### 4.2 修改（薄挂接）

| 文件 | 改动 |
|------|------|
| `config.py` + `env_flags.py` | `wiki_storm_report_enabled`（默认 **false**）；可选 `wiki_storm_max_sections`、`wiki_storm_parallel` |
| `api/wiki.py` | `POST /storm/report` → 202 + task_id；`GET /storm/report/{project_id}` 读最终页/状态 |
| `worker/tasks.py` | `run_wiki_storm_report_task` + `dispatch_wiki_storm_report` |
| `api/_dispatch.py` / dispatcher | 与 deep_research 同款 accepted_response |
| `report.py` | **不改**主路径；可选共享 `project_report_path(..., template="storm")` |
| `agents/*` | **默认不复用**配方 Chemist；STORM「专家角色」= Prompt persona，避免与可行性门混淆。若要用 Inspector，仅作 **Polish soft audit** 新入口 `inspect_report_markdown`（另 PR） |

### 4.3 明确不改

- Wiki 页 CRUD、`compile_source`、卷宗 ensure/patch/refresh  
- 同步 `POST /dossier/report` / export  
- Claims / DOE 约束模块语义  

---

## 5. Phase 设计细则

### 5.1 Phase 1 — Outline（`storm_outline.py`）

1. **默认角色表**（可配置，非硬编码唯一集）：配方化学家 / 盐雾与耐久测试 / 工艺与 VOC 合规 / 专利规避 / 成本与供应。  
2. 每角色：基于 `topic + pack.requirements/formula` 生成 2–3 子问题 → `kb_index.search_chunks_hybrid` / `wiki.retrieve` / 可选 KG focal。  
3. Supervisor **不是** `InitializeAgent`；用本地 `StormConductor`（纯函数编排 + `services/llm.complete_structured`）。  
4. 输出 `ReportOutline`；`retrieval_queries` / `focal_entities` 必须非空（校验器拒绝空章）。  
5. 落盘 `reports/project-{id}-storm.outline.json`（App 维护，禁 LLM 覆盖 path）。

### 5.2 Phase 2 — Draft（`storm_draft.py` + Celery）

**单章 Prompt 输入（禁止全文）：**

1. 大纲树（标题+section_id+当前位置）  
2. `depends_on` 已完成章的 `summary`（默认仅直接前驱；无依赖则可并行）  
3. 本章 `retrieval_queries` 的 top-k 片段（`build_citation_prompt`）  
4. Pack 中与本章相关的 **只读数值摘录**（如 S1 指标行），标明「不得改写数字」

**并行策略（务实）：**

- MVP：**顺序 Chain**（按 `sections` 顺序，滑动窗最稳）  
- v1.1：`depends_on==[]` 的章用线程池/组任务并行（仍非 Celery chord；与现网一致）  
- 进度：`stage=drafting_section_{i}`，`progress = 0.2 + 0.6 * i/N`

### 5.3 Phase 3 — Polish（`storm_polish.py`）

1. 拼接 + 可选过渡句润色（旗标）  
2. `bind_citations`：剔除未绑定 `[^n]`；脚注只含真实 `CitationAnchor`  
3. Soft rules：调用现有 `feasibility` / acid_stability **只读警告列表**写入附录「规则审查」；**不**自动改配方  
4. `lint.run` 结构问题 → flags，不阻断落盘  
5. Persist：`kind=report`，`report_template=storm`，`disclaimer=draft_not_claims`

---

## 6. API / 进度契约

### 6.1 新接口

```http
POST /api/wiki/storm/report
{
  "project_id": "...",
  "topic": "硅烷转化膜盐雾 720h 技术可行性",
  "template_hint": "feasibility",   // 可选：引导章节偏好
  "max_sections": 8,
  "use_llm": true,
  "ensure_dossier": true
}
→ 202 {
  "task_id": "...",
  "status_url": "/api/tasks/{id}",
  "stream_url": "/api/tasks/{id}/stream",
  "disclaimer": "draft_not_claims"
}
```

旗标关 → 409（与现网 dossier report 一致）。

### 6.2 SSE stages（对齐 ThinkingTracker）

| stage | 含义 | 建议 progress |
|-------|------|---------------|
| `generating_outline` | 角色调研 + 大纲 | 0.15 |
| `drafting_section_N` | 第 N 章 | 0.2–0.8 |
| `stitching` | 缝合 | 0.85 |
| `linting` | 引用/规则 | 0.92 |
| `done` / failed | 终态 | 1.0 / — |

`data` 可带：`section_id`, `title`, `outline_section_count`。

---

## 7. 实施切片（供排期）

| 切片 | 人日 | 交付 | 风险 |
|------|------|------|------|
| **P0 Schema + 假 LLM 单测** | 1 | `storm_schema` + outline/draft 校验 | 低 |
| **P1 Outline 服务 + 旗标 API 骨架** | 2–3 | Phase1 + 落盘 outline JSON | 中（检索汇聚） |
| **P2 顺序分章 Celery + SSE** | 3–4 | 异步长文落 `*-storm.md` | 中 |
| **P3 citation_binder 生产化 + soft rules 附录** | 2 | 去幻觉引用 | 中 |
| **P4 Hub 进度 UI** | 2 | 复用 deep_research 时间线 | 低 |
| **P5 有限并行** | 2 | depends_on 调度 | 中 |

**不在首期：** 复刻 STORM 全文对话 UI、Celery chord、自动写 L1、默认开旗标。

---

## 8. 测试与验收

| 用例 | 期望 |
|------|------|
| 旗标关 | POST storm → 409 |
| 假 LLM outline | sections≥3；每章 queries 非空 |
| 分章 prompt | fixture 断言 **不含** 他章全文 |
| 滑动窗 | 第 2 章 prompt 含第 1 章 summary |
| 引用 | 伪造 `[^99]` 被 strip；真实 source_id 进 footnotes |
| 回归 | `generate_report(briefing)` / export MD / grayscale gate 仍绿 |
| Claims/DOE | storm 页 path 不进 bounds；Claims 无 `wiki:` |

---

## 9. 风险与决策点（请评审拍板）

1. **Schema 落点**：`domain/schemas.py` vs `services/wiki/storm_schema.py`？ → 建议后者。  
2. **Agents 复用**：Prompt persona vs 扩展 `InspectorAgent`？ → 建议 persona；Inspector 另 PR soft audit。  
3. **并行**：MVP 是否接受纯顺序？ → 建议是。  
4. **与现模板关系**：`storm` 是否复用 feasibility 切片作「强制附录表」？ → 建议 **正文 STORM + 文末附 Pack 确定性表**（防数字幻觉）。  
5. **字数/费用**：`max_sections` 默认 6、`target_word_count` 封顶，防止费用爆炸。

---

## 10. 评审通过后的首选实现顺序

```text
storm_schema → storm_outline (P1) → task+API SSE (P2 顺序草稿)
  → storm_polish + citation_binder (P3) → Hub 进度 →（可选）并行
```

旧路径 `POST /dossier/report` 保持「五表简报」灰度默认；STORM 为 **高级长文按钮**（旗标另开）。
