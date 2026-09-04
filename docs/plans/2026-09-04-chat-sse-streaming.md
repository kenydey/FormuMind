# Chat SSE 流式化改造计划(2026-09-04)

## 背景
问答 4 轮排查后:150s+ 无限挂 → 41s 封顶必响应。但 40s 级等待体感仍差。
根治方向 = **SSE 流式**:检索阶段提示 + 主回答逐 token 输出,首字 ~2s,
全程进度可见、无"超时失败"概念。deepseek stream + thinking disabled
已 CLI 验证(2.5s 首字, 36 delta/chunk)。

## 架构

```
前端 sendChat ──POST /api/chat/stream(SSE)──▶ FastAPI async 端点
      ▲                                              │
      │  data: {"type":"phase","phase":"retrieval"}  │ 同步重活在 to_thread
      │  data: {"type":"meta","kb_used":N,...}       │ (检索/澄清/召回)
      │  data: {"type":"token","delta":"..."} ×N     │ LLM 流式: worker 线程
      │  data: {"type":"done", ChatResponse 全量}    │ on_delta → asyncio.Queue
      └──────────────────────────────────────────────┘
```

## 事件协议(SSE, data: JSON)
- `phase`: retrieval | answering | claims(前端阶段指示)
- `meta`: {kb_used, sources 摘要, rewritten_query}(检索完成)
- `token`: {delta} — 主回答增量(disable_thinking)
- `done`: 全量 {answer, citations, claims, clarification, kb_used...}
- `error`: {message}
- 每 15s 空注释行保活

## 后端改动
| 文件 | 改动 |
|---|---|
| `services/llm.py` | + `_openai_compatible_stream(prompt, key, model, max_tokens, base_url, on_delta, disable_thinking) -> str`(openai stream=True, 累积+回调; 不自动重试) |
| `api/chat.py` | + `POST /api/chat/stream`(async def + StreamingResponse): prepare(检索/澄清/召回 top-k → prompt)在 to_thread; LLM 流式 worker 线程 on_delta→queue; claims(12s 超时)收尾; structured 请求回退整包 done |
| `api/chat.py` | 抽 `_resolve_answer_plan(req, settings)`(准备逻辑与旧端点共享; 旧 /api/chat 不动) |

## 前端改动
| 文件 | 改动 |
|---|---|
| `api.ts` | + `chatStream(req, onEvent, signal)`: fetch + ReadableStream 解析 SSE |
| `store/slices/searchSlice.ts` | sendChat 改流式: 占位 assistant 消息 + 逐 token append + done 落全量; 消息加 `streaming/phase` 标记 |
| `components/ResearchPanel.tsx` | streaming 中纯文本渲染(光标)+ 阶段小字; 完成后切 MarkdownMessage |

## 测试
- 后端 `test_chat_stream.py`: monkeypatch 假流 → TestClient 断言事件序列(token 累积/done/error/structured 回退)
- 前端: SSE 解析器单测 + sendChat 测试 mock chatStream
- 回归: 现有 chat 相关测试全绿

## 风险与决策
- 保留旧 `/api/chat`(回退/其他调用方); 前端切换后旧端点仍可用
- 首 token 45s 无输出 → error 事件(前端可重试)——流式不做 45s 总 deadline(持续输出即进度)
- 草稿检测: 流式结束判定 draft → 重试一次(重试也流式), 仍 draft 则发 error
- 孤儿线程/队列容量: queue(maxsize=256) 防 worker 阻塞; 客户端断开 → 生成器 close → 线程自然结束
