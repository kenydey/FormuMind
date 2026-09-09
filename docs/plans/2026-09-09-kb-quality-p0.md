# P0 实施计划 — 知识库检索/入库质量门控（Domain Profile）

状态：**Q0 已落地，Q1–Q6 待实施**（2026-09-09）  
范围：**仅 P0**（检索原生分类硬过滤 + 入库门校准 + 可审计打标）  
不做：pending 人工队列 UI、Wiki 只编译 trusted 全量改造、embedding 第三票、历史库回扫（→ P1/P2）  
开发约定：**直接在 `main` 切片落地**；旗标默认偏保守，可关回旧行为  
关联评估：会话「知识库质量优化」· Wiki [`2026-09-09-rag-llm-wiki-hybrid.md`](./2026-09-09-rag-llm-wiki-hybrid.md) · Hub [`2026-09-09-knowledge-hub-impl.md`](./2026-09-09-knowledge-hub-impl.md)

---

## 1. P0 目标与成功标准

### 1.1 目标

把「主题关键词白名单」升级为 **双轨门控**：

1. **Pull**：各检索源尽量使用 **原生 taxonomy**（arXiv cat / OpenAlex concept / CPC·IPC）按 `ProductDomain` 收口。  
2. **Sanitize**：入库闸门用 **taxonomy 命中 ∪ 主题词 ∪ min_relevance**；专利不再无条件豁免。  
3. **Audit**：Evidence / 入库结果带上 `domain_tags` 与可追溯查询指纹，便于 Hub「资料」排障。

### 1.2 成功标准（DoD）

| # | 标准 |
|---|------|
| D1 | 选定 `ProductDomain` 后，arXiv / OpenAlex / 专利流查询带上该域的 category/concept/CPC（可单测断言 query/filter 字符串） |
| D2 | `kb_ingest_min_relevance` 默认 **> 0**；旗标可关回 0 |
| D3 | 主题过滤对专利改为 **CPC/域标签命中或主题词命中**，不再 `kind==patent` 一律放行 |
| D4 | `Evidence`（或并行 meta）含 `domain_match` / `domain_tags`；ingest 写 `ingest_audit` 一行（query、domain、accepted/skipped、原因） |
| D5 | 旗标全关或 Domain Profile 未启用时，行为与现网兼容（回归检索/入库测试通过） |
| D6 | 用歧义词（如「720」+ 钝化域）冒烟：医学噪声占比相对 P0 前明显下降（记录前后样例，不要求精确 %） |

### 1.3 非目标（P0 不做）

- Hub 内 pending 审核工作流 UI  
- Wiki 强制 `trust_tier=primary` 才编译（P1）  
- Scholar 默认关闭（可先降权，不硬删源）  
- 全库历史 Wiki 清洗  
- 向量语义主题门  

---

## 2. Domain Profile 契约

### 2.1 绑定现有枚举（禁止平行 taxonomy）

沿用 `ProductDomain`（`backend/app/domain/schemas.py`）：

| ProductDomain | 中文 | P0 默认检索侧重 |
|---------------|------|-----------------|
| `anticorrosion_coating` | 防腐蚀涂料 | 涂料/树脂/防腐 · CPC `C09D` 等 |
| `degreaser` | 脱脂剂 | 清洗/脱脂 · 表面处理化学 |
| `surface_treatment` | 表面处理剂 | 钝化/磷化/转化膜 · CPC `C23C`/`C23F` |
| `autodeposition_coating` | 自沉积涂料 | Autophoretic/自沉积 · `C09D` + 工艺词 |

可选叠加：`Substrate`（Mg/Al/钢）→ 仅注入 query 侧写，不单独建 Profile。

### 2.2 Profile 数据结构（代码落点建议）

新建例如 `backend/app/services/domain_profile.py`（或 `backend/app/domain/search_profiles.py`）：

```python
@dataclass(frozen=True)
class DomainSearchProfile:
    domain: ProductDomain
    arxiv_categories: tuple[str, ...]      # e.g. ("cond-mat.mtrl-sci", "physics.chem-ph")
    openalex_concept_ids: tuple[str, ...] # OpenAlex concept OpenAlex IDs
    s2_fields_of_study: tuple[str, ...]   # Semantic Scholar
    cpc_prefixes: tuple[str, ...]         # "C23", "C09D", "C25D"
    ipc_codes: tuple[str, ...]            # expander 默认 IPC 覆盖/收紧
    keyword_allow: tuple[str, ...]        # 中英主题锚（入库门）
    keyword_deny: tuple[str, ...]         # 跨域拒词（医学种植体等，按域定制）
    source_policy: dict[str, str]         # provider -> "primary"|"support"|"off"
```

**P0 内置四套常量 Profile**；环境变量只做开关与微调，不要求首期做可视化编辑器。

### 2.3 设置 / 旗标（P0）

| 旗标 / 配置 | 默认 | 说明 |
|-------------|------|------|
| `domain_profile_search` | **True** | 检索层套用 Profile |
| `kb_ingest_topic_filter` | True（已有） | 入库主题门；语义升级为 Profile 词表 |
| `kb_ingest_min_relevance` | **0.45**（现 0.0→上调） | 可用 env 调回 0 |
| `kb_ingest_patent_exempt` | **False**（行为变更） | 旧「专利豁免」关闭；CPC/词命中才过 |
| `arxiv_domain_filter` | True（已有） | 改为读 Profile.categories，而非写死三类 |
| `openalex_concept_filter` | **True**（新） | OpenAlex `filter=concepts.id:...` |
| `patent_cpc_filter` | **True**（新） | 专利查询附加 CPC；结果无 CPC 时降权不直接丢（除非 deny） |

设置 UI：P0 可只在 **环境旗标面板** 暴露布尔项；「当前项目 ProductDomain」已存在则自动选 Profile。

---

## 3. Provider 过滤矩阵（P0 必须改的）

| Provider | P0 动作 | 实现要点 | 文件（预期） |
|----------|---------|----------|--------------|
| **arXiv** | 按 Profile 替换写死 cat | `cat:A OR cat:B` 来自 Profile | `literature.search_arxiv` |
| **OpenAlex** | **新增** concept filter | `filter=concepts.id:C1|C2` + 仍可 OA 约束 | `search_providers.search_openalex` |
| **Semantic Scholar** | `_ALLOWED_S2_FIELDS` → Profile | 按域收紧，去掉过宽的 `medicine`（防腐域） | `literature` S2 路径 |
| **EPO / patent_client** | 强制带 Profile `cpc`/`ipc` | expander `_DEFAULT_IPC` 改为 Profile.ipc_codes | `query_expander` · `literature` EPO |
| **Google Patents / SerpAPI** | query 追加 `CPC=(…)` 或等价；解析结果 CPC 后滤 | 无 CPC 元数据 → `domain_match=weak` | SerpAPI patent 适配 |
| **SureChEMBL** | 保持化学向；deny 词后滤 | 不作为医学噪声主因 | content search |
| **Google Scholar** | **不硬过滤**；`source_policy=support` 降权 | 入库更严 | scoring |
| **PubChem** | 不动（非文献源） | — | — |

### 3.1 OpenAlex Concept 包（P0 初值，已用 API 确认 ID）

写入 `search_profiles.py` 的稳定短 ID（不含 URL 前缀）：

| Concept | OpenAlex ID | 用于域 |
|---------|-------------|--------|
| Materials science | `C192562407` | 全域底 |
| Corrosion | `C20625102` | 防腐 / 表面 / 自沉积 |
| Coating | `C2781448156` | 防腐 / 自沉积 |
| Surface modification | `C115537861` | 表面 / 自沉积 |
| Metallurgy | `C191897082` | 防腐 / 表面 |
| Passivation | `C33574316` | 表面处理 |
| Cleaning agent | `C165460524` | 脱脂 |
| Degreasing | `C150042643` | 脱脂 |
| Electrochemistry | `C52859227` | 脱脂 / 自沉积 |
| Nonionic surfactant | `C2994558038` | 脱脂 |

**排除**：植入物、骨科、临床医学大类 → 各域 `keyword_deny`（含 biomedical / implant 等）。

### 3.2 CPC 前缀初值（按域）

| Domain | CPC 前缀（起点） |
|--------|------------------|
| `anticorrosion_coating` | `C09D`, `C08G`, `C23F` |
| `degreaser` | `C23G`, `C11D`, `C23F` |
| `surface_treatment` | `C23C`, `C23F`, `C25D` |
| `autodeposition_coating` | `C09D`, `C25D`, `C23C` |

与现网 `_DEFAULT_IPC = C09D / C09D175/04 / C08G18/00 / C23F` 对齐并 **按域拆分**，避免脱脂项目一直用涂料 IPC。

---

## 4. 数据与打标

### 4.1 Evidence 扩展（轻量）

在 `Evidence` 或检索合并阶段写入可选字段（Pydantic 增可选，兼容旧客户端）：

| 字段 | 含义 |
|------|------|
| `domain_tags` | `["surface_treatment","cpc:C23C"]` |
| `domain_match` | `strong` \| `weak` \| `none` |
| `taxonomy_source` | `arxiv` \| `openalex` \| `cpc` \| `lexical` \| `none` |

打分：`search_scoring` 对 `strong` 加权、`none` 降权；`keyword_deny` 命中 → 直接丢弃或 `none`。

### 4.2 入库审计（最小表或 JSON 日志）

**P0 推荐**：SQLite 表 `kb_ingest_audit`（或 append-only JSONL under `data/audit/` 若想更快）：

| 列 | 说明 |
|----|------|
| `id`, `created_at` | |
| `project_id`, `domain` | |
| `query_fingerprint` | hash(规范化 query + domain) |
| `evidence_id`, `source` | |
| `action` | `accept` \| `skip` |
| `reason` | `low_relevance` \| `topic_miss` \| `deny` \| `dup` \| `ok` |
| `domain_match` | |

**不可被 Hub「删除资料」级联删掉**（审计保留；可只做软引用）。

### 4.3 SourceDocument / Wiki（P0 最小）

- `SourceDocument`：meta JSON 增加 `domain`、`domain_match`、`ingest_query`（若已有 meta 列则写入，避免大迁移）。  
- Wiki：P0 **只**在 compile 时把 `domain_tags` 写入 front-matter（若有）；**不**做 trusted 编译门（P1）。

---

## 5. 入库闸门伪代码（替换现逻辑）

```text
select_ingest_targets(evidence, domain, query):
  for e in evidence:
    if not fetchable(e): skip(unfetchable)
    if dup(e): skip(dup)
    if relevance < min_relevance: skip(low_relevance)   # default 0.45
    if deny_hit(e, profile): skip(deny)
    if e.domain_match == strong: accept
    elif e.domain_match == weak and lexical_hits >= 1: accept
    elif lexical_hits >= 2: accept                      # 无 taxonomy 时回退
    elif is_patent and cpc_prefix_hit(e, profile): accept
    else: skip(topic_miss)   # 含专利；不再 blind exempt
```

手动「入库全文」：可保留 `skip_topic_filter=True`，但 **仍写 audit**，并在 UI 标注「用户强制入库」。

---

## 6. 切片与排期

| 切片 | 内容 | 估时 | 验收 |
|------|------|------|------|
| **Q0** ✅ | `DomainSearchProfile` 四域常量 + 单元测试映射 | 0.5–1d | 每域含 arxiv/cpc/keywords |
| **Q1** | arXiv / S2 / expander IPC 按 Profile | 1d | 查询字符串单测 |
| **Q2** | OpenAlex concept filter + Patents CPC 附加/后滤 | 1–1.5d | mock API 单测；无 concept 时 degrade 不炸 |
| **Q3** | Evidence 打标 + scoring 加权 | 0.5–1d | 合并结果含 domain_match |
| **Q4** | 入库门改造 + min_relevance 默认 + 专利豁免关闭 | 1d | `test_kb_ingest_*` 更新 |
| **Q5** | ingest audit 落库 + 旗标进 env_flags | 0.5–1d | 审计行可查；旗标可关 |
| **Q6** | 歧义词冒烟记录 + 文档勾选 | 0.5d | 计划 D6 样例附录 |

**合计：约 5–7 人日。**  
顺序：Q0 → Q1 → Q2 → Q3 → Q4 → Q5 → Q6。

---

## 7. 文件落点（预期）

| 路径 | 变更 |
|------|------|
| `backend/app/domain/search_profiles.py`（新） | Profile 常量与 `get_profile(domain)` |
| `backend/app/config.py` / `env_flags.py` | 新旗标与 min_relevance 默认 |
| `backend/app/services/literature.py` | arXiv / S2 |
| `backend/app/services/search_providers.py` | OpenAlex filter |
| `backend/app/services/deep_research/query_expander.py` | 按域 IPC |
| `backend/app/services/search_scoring.py` | domain_match 加权 |
| `backend/app/services/kb_ingest.py` | 闸门 + audit |
| `backend/app/db/models.py` + alembic | `kb_ingest_audit`（若选表） |
| `backend/app/domain/schemas.py` | Evidence 可选字段 |
| `backend/tests/test_domain_profile_*.py` 等 | 单测 |
| `docs/plans/2026-09-09-kb-quality-p0.md` | 本文 |

前端 P0：**不强制** Hub 改版；可选在资料行展示 `domain_match` badge（若成本低可并于 Q5）。

---

## 8. 测试计划

| 测试 | 断言 |
|------|------|
| `test_profile_arxiv_query_contains_cats` | surface_treatment → 含 C23 相关 cat 或材料 cat |
| `test_profile_ipc_differs_by_domain` | degreaser IPC ≠ coating 默认全集简单相等 |
| `test_openalex_filter_param` | 请求带 concepts.id（mock httpx） |
| `test_ingest_skips_low_relevance` | min_relevance=0.45 时低分跳过 |
| `test_ingest_patent_needs_domain_or_cpc` | 无 CPC/主题的专利 skip；有 C23C 则 accept |
| `test_deny_keyword_blocks` | 域 deny 词命中 skip |
| `test_flags_off_compat` | domain_profile_search=False 时 arXiv 仍可用旧全局 filter |
| `test_audit_row_written` | accept/skip 均有 audit |
| 回归 | 现有 `test_kb_ingest_*` · literature 冒烟 |

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| OpenAlex concept ID 选错 → 召回骤降 | 可关 `openalex_concept_filter`；先 shadow 日志「若过滤会丢多少」再默认开 |
| CPC 过严 → 专利变少 | 前缀宜宽（C23*）+ weak 通道；Serp 无 CPC 时降权不丢 |
| min_relevance=0.45 误杀 | 配置可调；观察一周再调到 0.35/0.5 |
| 行为变更惊吓用户 | 变更说明进 CHANGELOG；env 一键关 Profile |
| 与 Knowledge Hub 删除冲突 | audit 表不级联删除 |

---

## 10. 与 P1 交接

P0 完成后自然接：

1. `trust_tier` + pending 队列（Hub 资料 Tab）  
2. Wiki 仅 compile trusted  
3. Scholar 默认 support/off  
4. 历史证据回打 `domain_tags`  

---

## 11. 状态

- [x] 方向认可（原生 taxonomy + 产品域双轨）  
- [x] P0 实施计划成文  
- [x] **Q0** `DomainSearchProfile` 四域常量 + 单测（`backend/app/domain/search_profiles.py`）  
- [ ] Q1–Q6 代码落地  
- [ ] DoD D1–D6 验收  
- [x] Q0 推送 `main`  

---

## 12. 实施口令

确认开工后按切片执行：

```text
先 Q0 DomainSearchProfile → Q1/Q2 检索硬过滤 → Q3 打标 → Q4/Q5 入库+审计 → Q6 冒烟
```
