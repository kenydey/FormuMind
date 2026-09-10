# Chat 原生化学 Tool Calling 设计

> 状态：待用户审阅规格  
> 日期：2026-09-10  
> 来源：DarkChuang「对话内自动调化学工具」能力移植意向；协议选 **原生 function/tool calling**，执行层走 FormuMind `chemtools`。

## 1. 背景与目标

### 1.1 问题

FormuMind 已有确定性化学网关 `backend/app/services/chemtools.py`（描述符、官能团、名称解析、专利/安全筛等），并通过 `/api/chemical/*` 暴露。聊天路径（`/api/chat`、`/api/chat/stream`）在拆除 ChemCrow ReAct 后，**不会**在对话中自动调用这些工具；模型常口述数值，与「算出来的」结果脱节。

DarkChuang 用 prompt 逼 JSON + 字符串匹配实现聊天工具循环；该协议脆弱，且与 FormuMind 刻意拆除的 agent 风格同构，**不予照搬**。

### 1.2 目标

在聊天中启用 **供应商原生 tool calling**：模型决定是否调用 → 后端执行 `chemtools` → 工具结果回灌 → 最终自然语言回答。主路径为 **SSE 全链路流式**（工具事件 + 最终答案逐 token）。

### 1.3 非目标（明确不做）

- 3D 构象 / 3dmol / MediaModal
- NMR / 谱图 Agentic 流程
- ChromaDB 或其它 RAG 替换
- Prompt 内 JSON 伪工具协议（DarkChuang 风格）
- 恢复 ChemCrow / 通用 ReAct agent 框架
- v1 Anthropic 原生 tools（可后续迭代）
- 结构图 PNG 服务端生成（已有 smiles-drawer / OCSR）

## 2. 需求摘要（已确认）

| 项 | 选择 |
|---|---|
| 协议 | 原生 function / tool calling |
| 工具范围 | 对齐 `chemtools.availability().capabilities` 中对聊天有意义的能力，并包装 `safety_flags` / `chemical_profile` |
| 流式 | 全链路：`tool_start` / `tool_result` + 最终 `token` 流 |
| 降级 | v1 仅 OpenAI 兼容供应商启用；其它供应商静默直答；设置页可见是否启用 |

## 3. 架构

### 3.1 推荐结构（独立编排层）

```
chat/stream (api/chat.py)
    │
    ├─ 现有：rewrite / KB augment / clarification / BM25 召回 → prompt
    │
    └─ chat_chem_tools 编排（新模块）
           ├─ should_enable_tools(provider, flags) → bool
           ├─ openai_tool_schemas() → list[Tool]
           ├─ execute_tool(name, args) → dict  （调 chemtools）
           └─ run_tool_loop_stream(...) → yield SSE-ready events
                    │
                    ├─ OpenAI-compatible chat.completions（tools + stream）
                    ├─ 解析 tool_calls → execute → 追加 messages
                    └─ 最终无 tool_calls 时，把 assistant 内容以 token 事件吐出
```

**不**把 tool loop 直接堆进巨型 `llm.py`；`llm.py` 仅增加可复用的「带 tools 的 OpenAI 兼容请求/流式」薄封装（若现有 `_openai_compatible_stream` 不便扩展）。

### 3.2 启用条件（全部满足）

1. `chemtools_enabled`（已有 EnvFlag）为真  
2. 新 EnvFlag `chat_chem_tools_enabled` 为真（**默认 true**）  
3. 当前 `llm_provider` ∈ 现有 `_OPENAI_COMPAT_PROVIDERS`（openai / xai / groq / deepseek / qwen / moonshot / minimax / custom）  

任一不满足 → **静默**走现有直答流式路径（无工具事件）。设置页通过 `/api/chemical/tools` 或 settings/env-flags 展示「聊天化学工具：已启用 / 当前供应商不支持 / 网关关闭」。

### 3.3 工具清单

| Tool name | 参数（概念） | 实现 |
|---|---|---|
| `name_to_smiles` | `name: str` | `chemtools.name_to_smiles` |
| `name_to_cas` | `name: str` | `chemtools.name_to_cas` |
| `func_groups` | `smiles: str` | `chemtools.func_groups` |
| `mol_descriptors` | `smiles: str` | `chemtools.mol_descriptors` |
| `mol_similarity` | `smiles_a`, `smiles_b` | `chemtools.mol_similarity` |
| `synthetic_accessibility` | `smiles: str` | `chemtools.synthetic_accessibility` |
| `patent_check` | `smiles: str` | `chemtools.patent_check` |
| `explosive_check` | `cas: str` | `chemtools.explosive_check` |
| `safety_flags` | `smiles?`, `cas?` | `chemtools.safety_flags` |
| `chemical_profile` | `q: str` | `chemtools.chemical_profile` |

能力不可用时：执行层返回 `{ "ok": false, "hint": "<availability hint>" }`，不抛未捕获异常；模型可改用其它 tool 或说明限制。

## 4. SSE 协议扩展

现有事件（保持兼容）：`phase` / `meta` / `token` / `done` / `error`。

新增：

```ts
| { type: "phase"; phase: "retrieval" | "tools" | "answering" | "claims" }
| { type: "tool_start"; name: string; args: Record<string, unknown> }
| { type: "tool_result"; name: string; ok: boolean; summary: string }
```

约定：

- 进入 tool loop 前可发 `phase: "tools"`；开始生成最终答案时发 `phase: "answering"`（与现网一致）。
- `tool_result.summary` 为短文本（如「LogP=1.2, TPSA=63」），完整 JSON 只进入模型 messages，不强制进 UI。
- `done` 形状不变；可选增加 `tools_used: string[]`（非破坏性，前端可忽略）。

前端（`api.ts` `ChatStreamEvent` + `searchSlice.sendChat`）：

- 处理 `tool_start` / `tool_result`：更新占位 assistant 的轻量状态（如 `phase` 或 `toolStatus`）。
- 最终 `content` 仍只累积 `token` / `done.answer`，不把工具 JSON 写入可见正文。

## 5. Tool loop 行为

1. 构造 messages：`system`（现有化学/配方助手说明 +「涉及精确描述符/CAS/安全须先调工具，禁止编造数值」）+ 用户/历史 + 检索上下文（沿用现有 `_chat_prompt` 内容，可拆成 system/user）。
2. 调用 OpenAI 兼容 API，传入 `tools` + `tool_choice: "auto"`；优先 **stream**。
3. 若流中出现 `tool_calls`：聚合完整调用 → 对每个 call 发 `tool_start` → `execute_tool` → `tool_result` → 追加 `role=tool` 消息。
4. 重复直至：无 tool_calls，或达到 **`chat_chem_tools_max_rounds`（默认 4）**。
5. 最终 assistant 文本：若该轮已是 stream 文本则直接转发 `token`；若供应商在 tool 轮后需非流式收尾，则对最后一轮用 stream 再要一次「无 tools / tool_choice none」的回答。
6. 超时：单次 chemtools 沿用网关超时；整轮 chat 沿用现有 LLM timeout；失败发 `error` 或降级拼接已有部分答案。

并行：同一轮多个 `tool_calls` 可并行执行（ThreadPool，受 chemtools executor 约束），按 API 要求的顺序写回 messages。

## 6. 非流式 `/api/chat`

同一编排的同步版本（无 SSE）：跑完 tool loop 后返回完整 `ChatResponse`。用于测试与结构化回退；主 UX 仍是 stream。

结构化输出（`response_format=structured`）v1：**不进入 tool loop**（与现网「结构化不走 token 流」一致），避免 JSON schema 与 tools 冲突；可在后续迭代再定。

## 7. 配置

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `chat_chem_tools_enabled` | EnvFlag / settings | `true` | 总开关（仍受 `chemtools_enabled` 与供应商约束） |
| `chat_chem_tools_max_rounds` | int | `4` | 最大 tool 往返轮数 |

## 8. 测试计划

- 单元：`execute_tool` 对各 tool 的参数校验、不可用 hint、异常吞并。
- 单元：`should_enable_tools` 在 anthropic / 关 flag / 关 chemtools 时为 false。
- 集成（mock OpenAI）：假 tool_calls → 假 chemtools → 最终文本；断言 SSE 事件序 `phase(tools) → tool_start → tool_result → phase(answering) → token* → done`。
- 回归：关 `chat_chem_tools_enabled` 时行为与现网 stream 一致。
- 手动：DeepSeek/OpenAI 兼容端问「阿司匹林的 LogP 和官能团」，确认调用 `name_to_smiles`/`mol_descriptors`/`func_groups` 且回答引用工具结果。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 部分「兼容」端点声称 OpenAI 但不支持 tools | 捕获 4xx → 本请求静默降级直答，并打日志；设置页仍显示「本供应商声明兼容」 |
| 模型过度调工具拖慢首字 | max_rounds=4；`chemical_profile` 合并多查询；UI 显示 tool 进度 |
| 与 KB 检索延迟叠加 | 保持现有 retrieval 在 tools 之前；tools 不替代 RAG |
| 数值幻觉 | system 约束 + 有 tool 结果时优先引用 |

## 10. 实现切片（供后续 plan）

1. `chat_chem_tools.py`：schema + execute + enable 判定  
2. LLM 薄封装：带 tools 的 stream/非 stream  
3. `chat/stream` 接入 + SSE 类型  
4. EnvFlag / settings  
5. 前端事件处理与轻量 UI  
6. 测试  

---

## 修订记录

- 2026-09-10：初稿，用户批准设计方向（方案 1 + 需求表）。
