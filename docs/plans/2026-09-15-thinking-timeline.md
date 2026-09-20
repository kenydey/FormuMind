# 维 2：长任务思考链路（Thinking Timeline）独立方案 + 实现说明

> 状态：**实施中**（2026-09-15）  
> 约束：保留 Celery + 现有 `GET /api/tasks/{id}/stream`；不引入 ARQ/LangGraph  
> 目标：推荐 / 寻优 / DOE / 深度研究运行时，前端中央可见步骤级思考流，消除黑盒感

## 决策（默认锁定）

1. **事件载体**：继续用 `TaskProgressEvent`；思考步骤放在 `data.thinking: ThinkingStep[]`（全量快照，非增量协议）
2. **兼容性**：`stage` / `message` / `progress` 语义不变；旧前端忽略 `data.thinking`
3. **UI 入口**：ActionsPanel 推荐 Modal、寻优 Modal；深度研究沿用 ResearchPanel / Notification 详情
4. **不做**：第二套 SSE、Vue 组件移植、把 Actions 收成 Agent Chat

## ThinkingStep 形状

```json
{
  "id": "retrieve",
  "kind": "stage|thought|tool",
  "title": "正在检索表面活性剂库…",
  "detail": "可选补充说明",
  "status": "pending|running|done|error"
}
```

## 后端发射点

| 任务 | 阶段示例 |
|------|----------|
| recommend | retrieve → grade → recommend |
| optimize | init → propose → evaluate → converge（由 progress 文案映射） |
| doe_cycle | load → candidates → design → write |
| deep_research | 沿用 research_graph stage，附 thinking 快照 |

## 前端

- `ThinkingTimeline` 可折叠时间线
- store：`taskThinking: ThinkingStep[]`，流式事件覆盖更新，任务结束清空
- 推荐 Modal 在现有 CRAG 条下方挂载时间线

## 测试

- `data.thinking` 序列化进 Redis/SSE
- recommend 至少发出 retrieve/grade/recommend 三阶段
- ThinkingTimeline 渲染 running/done
