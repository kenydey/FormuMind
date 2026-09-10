# Chat 原生化学 Tool Calling 设计

> 状态：待用户审阅规格（修订 2）  
> 日期：2026-09-10  
> 来源：DarkChuang「对话内自动调化学工具」能力移植意向；协议选 **原生 function/tool calling**；执行层走 FormuMind 既有 `chemtools` / MolScribe / SureChemBL / RDKit 结构检索。

## 1. 背景与目标

### 1.1 问题

FormuMind 已有确定性化学能力（`chemtools`、`structure_recognize`/MolScribe、`surechembl_*`、`structure_search`），并通过 `/api/chemical/*` 等暴露。聊天路径（`/api/chat`、`/api/chat/stream`）在拆除 ChemCrow ReAct 后，**不会**在对话中自动调用这些工具；模型常口述数值，与「算出来的」结果脱节。

DarkChuang 用 prompt 逼 JSON + 字符串匹配实现聊天工具循环；该协议脆弱，且与 FormuMind 刻意拆除的 agent 风格同构，**不予照搬**。

### 1.2 目标

在聊天中启用 **供应商原生 tool calling**：模型决定是否调用 → 后端执行既有化学服务 → 工具结果回灌 → 最终自然语言回答。主路径为 **SSE 全链路流式**（工具事件 + 最终答案逐 token）。

### 1.3 非目标（明确不做）

- 3D 构象 / 3dmol / MediaModal
- NMR / 谱图 Agentic 流程
- ChromaDB 或其它 RAG 替换
- Prompt 内 JSON 伪工具协议（DarkChuang 风格）
- 恢复 ChemCrow / 通用 ReAct agent 框架
- v1 Anthropic 原生 tools（可后续迭代）
- 结构图 PNG **服务端绘制**（已有 smiles-drawer；与 MolScribe **识别**不同）
- 让模型在 tool args 中传 base64 大图或任意文件系统路径
- SureChemBL 实施例草稿确认 / KG ingest 等写操作（只读查询类 tool）

## 2. 需求摘要（已确认）

| 项 | 选择 |
|---|---|
| 协议 | 原生 function / tool calling |
| 工具范围 | **档 B**：chemtools 能力全集包装 + SureChemBL 只读查询 + 额外 RDKit 结构检索 + MolScribe（仅 `image_ref`） |
| 流式 | 全链路：`tool_start` / `tool_result` + 最终 `token` 流；MolScribe 时 UI 提示「结构识别中」 |
| 降级 | v1 仅 OpenAI 兼容供应商启用工具循环；其它供应商静默直答；设置页可见是否启用 |
| MolScribe | `recognize_structure(image_ref)`；`image_ref` 仅本轮已上传附件 id/sha |

## 3. 架构

### 3.1 推荐结构（独立编排层）

```
chat/stream (api/chat.py)
    │
    ├─ 现有：rewrite / KB augment / clarification / BM25 召回 → prompt
    ├─ 注入本轮可引用附件清单（image_ref 白名单）供模型与 execute 校验
    │
    └─ chat_chem_tools 编排（新模块）
           ├─ should_enable_tools(provider, flags) → bool
           ├─ openai_tool_schemas(ctx) → list[Tool]  （按 flag/可用性过滤）
           ├─ execute_tool(name, args, ctx) → dict
           └─ run_tool_loop_stream(...) → yield SSE-ready events
```

**不**把 tool loop 直接堆进巨型 `llm.py`；`llm.py` 仅增加可复用的「带 tools 的 OpenAI 兼容请求/流式」薄封装。

执行层按 tool 分发到：

| 后端 | 模块 |
|---|---|
| chemtools | `services/chemtools.py` |
| MolScribe | `services/structure_recognize.recognize_structure_image`（Celery `molscribe` 队列） |
| SureChemBL | `surechembl_lookup` / `surechembl_alternatives` / `literature.search_surechembl_content`（或 client） |
| RDKit 结构库 | `structure_search.substructure_hits` / `scaffold_substitutes`；`moljson.validate_smiles` |

### 3.2 启用条件

**工具循环总开关**（全部满足）：

1. `chemtools_enabled` 为真（聊天化学工具总闸；即便个别 tool 不依赖 RDKit 也沿用此产品开关，避免绕过网关策略）  
2. 新 EnvFlag `chat_chem_tools_enabled` 为真（**默认 true**）  
3. 当前 `llm_provider` ∈ `_OPENAI_COMPAT_PROVIDERS`

**单工具可用性**（schema 可仍列出，或动态省略；执行时必须再校验）：

| Tool 族 | 额外条件 |
|---|---|
| RDKit 本地 | `rdkit_available()` |
| PubChem 名解析 | `pubchem_available()` |
| molbloom 专利 | `molbloom_available()` |
| SureChemBL | `surechembl` flag + 网络 |
| MolScribe | `ocsr_enabled` + molscribe worker 可达；且 `ctx` 中有合法 `image_ref` |

任一总开关不满足 → **静默**直答。单工具失败 → `{ok:false, hint}`，不中断整轮（除非 LLM 层硬错误）。

### 3.3 工具清单

#### A. chemtools（原档 3）

| Tool name | 参数 | 实现 |
|---|---|---|
| `name_to_smiles` | `name` | `chemtools.name_to_smiles` |
| `name_to_cas` | `name` | `chemtools.name_to_cas` |
| `func_groups` | `smiles` | `chemtools.func_groups` |
| `mol_descriptors` | `smiles` | `chemtools.mol_descriptors` |
| `mol_similarity` | `smiles_a`, `smiles_b` | `chemtools.mol_similarity` |
| `synthetic_accessibility` | `smiles` | `chemtools.synthetic_accessibility` |
| `patent_check` | `smiles` | `chemtools.patent_check` |
| `explosive_check` | `cas` | `chemtools.explosive_check` |
| `safety_flags` | `smiles?`, `cas?` | `chemtools.safety_flags` |
| `chemical_profile` | `q` | `chemtools.chemical_profile` |

#### B. 扩展 RDKit / 材料结构检索

| Tool name | 参数 | 实现 |
|---|---|---|
| `validate_smiles` | `smiles` | `moljson.validate_smiles`（返回 valid / canonical / 简要 meta） |
| `substructure_search` | `smarts`, `top_k?` | `structure_search.substructure_hits` |
| `scaffold_substitutes` | `smiles`, `top_k?` | `structure_search.scaffold_substitutes` |

#### C. SureChemBL（只读）

| Tool name | 参数 | 实现 |
|---|---|---|
| `surechembl_lookup` | `q` | `surechembl_lookup.lookup_surechembl` |
| `surechembl_similar` | `smiles`, `top_k?` | `surechembl_alternatives.fetch_surechembl_alternatives`（结果截断摘要） |
| `surechembl_search` | `query`, `limit?` | `literature.search_surechembl_content` 或 client `content_search`；**默认 limit≤5**，summary 仅标题/doc_id/url，避免与联邦检索重复灌全文 |

#### D. MolScribe（附件引用）

| Tool name | 参数 | 实现 |
|---|---|---|
| `recognize_structure` | `image_ref: str` | 解析白名单附件 → 读字节 → `recognize_structure_image(...)` |

**`image_ref` 规则（硬约束）：**

1. 仅允许本轮请求上下文 `ChatToolContext.allowed_image_refs` 中的值。  
2. 来源优先级（实现时取并集，去重）：  
   - 请求体 `structure.image_sha`（若前端已跑过 `/api/chemical/structure` 并带回 sha）  
   - `attachment_source_ids` 中可解析为本地图片附件的 id  
   - 本轮上传缓存中的临时图 id（若实现聊天直传图；无则仅前两项）  
3. **拒绝**：任意绝对路径、`..`、http(s) URL、非白名单 sha、base64 正文。  
4. 若白名单为空：该 tool 可不放入 schema，或执行时立刻 `{ok:false, hint:"本轮无结构图附件"}`。  
5. 超时：使用 `settings.molscribe_timeout_s`（现网默认 180s）；SSE 在 `tool_start` 时 `name=recognize_structure`，前端文案固定或映射为「结构识别中…」。  
6. 若请求已带完整 `structure.smiles` 且用户未要求重识别：system 提示优先使用已有 SMILES，避免重复冷启动 MolScribe。

## 4. SSE 协议扩展

现有事件（保持兼容）：`phase` / `meta` / `token` / `done` / `error`。

新增 / 扩展：

```ts
| { type: "phase"; phase: "retrieval" | "tools" | "answering" | "claims" }
| { type: "tool_start"; name: string; args: Record<string, unknown>; label?: string }
| { type: "tool_result"; name: string; ok: boolean; summary: string }
```

约定：

- `recognize_structure` 的 `tool_start.label`（或前端按 name 映射）=「结构识别中」。  
- `tool_result.summary` 短文本；完整 JSON 只进模型 messages。  
- `done` 可选 `tools_used: string[]`。  
- MolScribe 长时间运行期间保持连接；若代理超时，需与现有 SSE 重连策略对齐（前端已有 task SSE 重连经验，chat stream 若断线按现网错误处理，不在本规格发明新协议）。

前端（`ChatStreamEvent` + `sendChat`）：处理 `tool_start` / `tool_result` 更新 `toolStatus`；正文只累积 `token` / `done.answer`。

## 5. Tool loop 行为

1. 构造 `ChatToolContext`：allowed_image_refs、structure 摘要、flags。  
2. messages：system（配方/化学助手 +「精确数值/鉴定/结构须先调工具，禁止编造」+ 若有附件则列出可用 `image_ref` 列表）+ 用户/历史 + 检索上下文。  
3. OpenAI 兼容 API：`tools` + `tool_choice: "auto"`；优先 stream。  
4. 出现 `tool_calls` → `tool_start` → `execute_tool` → `tool_result` → 追加 `role=tool`。  
5. 直到无 tool_calls 或达到 **`chat_chem_tools_max_rounds`（默认 4）**。  
6. 最终文本 stream 为 `token`；必要时最后一轮 `tool_choice: "none"`。  
7. 超时：chemtools/SureChemBL 用网关短超时；`recognize_structure` 用 `molscribe_timeout_s`；LLM 用现有 timeout。  
8. 并行：同轮多个 tool_calls 可并行，**但** `recognize_structure` 建议串行或单独限流（GPU/worker 冷启动重）。

## 6. 非流式 `/api/chat`

同步同一编排。结构化输出（`response_format=structured`）v1：**不进入 tool loop**。

## 7. 配置

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `chat_chem_tools_enabled` | EnvFlag | `true` | 聊天工具循环总开关 |
| `chat_chem_tools_max_rounds` | int | `4` | 最大 tool 往返 |
| （已有）`chemtools_enabled` / `surechembl` / `ocsr_enabled` / `molscribe_timeout_s` | — | — | 单能力闸门与超时 |

设置页展示：聊天化学工具是否因供应商启用；SureChemBL / OCSR 是否可用。

## 8. 测试计划

- 单元：各 `execute_tool` 参数校验、白名单拒绝非法 `image_ref`、不可用 hint。  
- 单元：`should_enable_tools` 在 anthropic / 关 flag 时为 false。  
- 集成 mock：tool_calls 序含 `surechembl_lookup` / `substructure_search` / `recognize_structure`。  
- 回归：关 `chat_chem_tools_enabled` 与现网 stream 一致。  
- 手动：有结构图附件时问「识别这张图并查相似专利分子」→ `recognize_structure` → `surechembl_similar` / `mol_descriptors`。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 兼容端点不支持 tools | 4xx → 本请求静默直答 + 日志 |
| MolScribe 180s 拖垮体验 | SSE「结构识别中」；已有 smiles 则跳过；worker 未就绪立刻 hint |
| SureChemBL 与联邦检索重复 | `surechembl_search` 严限条数；system 说明优先用于专利化学鉴定/相似，不全文替代 RAG |
| 工具过多导致乱调 | schema description 写清何时用；max_rounds=4；`chemical_profile` / `surechembl_lookup` 合并场景 |
| `image_ref` 伪造读盘 | 硬白名单，禁止路径/URL/base64 |

## 10. 实现切片（供后续 plan）

1. `chat_chem_tools.py`：schema + execute + context/白名单 + enable  
2. LLM 薄封装：带 tools 的 stream/非 stream  
3. `chat/stream` 接入 + SSE 类型 + 附件 ref 注入  
4. EnvFlag / settings  
5. 前端 tool 状态 UI（含「结构识别中」）  
6. 测试  

---

## 修订记录

- 2026-09-10：初稿，用户批准设计方向（方案 1 + 需求表）。  
- 2026-09-10：修订 2 — 扩至档 B：SureChemBL 只读三件套、RDKit `validate_smiles`/`substructure_search`/`scaffold_substitutes`、MolScribe `recognize_structure(image_ref)` 白名单与长超时 SSE。
