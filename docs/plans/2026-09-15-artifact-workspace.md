# 维 1：中央自适应产物交付（Artifact Workspace）

> 状态：**已实现**（2026-09-15）  
> 约束：不引入 Docker sandbox / LangGraph / ARQ；不把 ActionsPanel 收成 Agent Chat  
> 目标：把推荐榜、DOE 方案、寻优曲线、深度报告等「活产物」从分散 Modal 收拢为可发现的工作区入口，并在推荐/DOE/寻优 Modal 内用双栏呈现控制区与产物

## 决策（默认锁定）

1. **无新后端**：`ProjectArtifact` 由 Zustand 现有字段派生（`leaderboard` / `doePlan` / `optimizationHistory` / `deepReport` / `loopReport`）
2. **入口**：顶栏「产物」抽屉（对称于「历史」），点击条目 → `openArtifact(id)` → 打开抽屉高亮 + 对应 Modal
3. **双栏**：推荐 / 寻优 / DOE（有方案时）采用 `ArtifactSplitLayout`（左控制+思考，右产物）
4. **保留** ActionsPanel 瓷砖与现有 Modal；Celery SSE / ThinkingTimeline 不动
5. **不做**：文件沙箱、工作区落盘、Vue 移植、Agent Chat 统一入口

## 前端

| 模块 | 职责 |
|------|------|
| `artifacts/projectArtifacts.ts` | `ArtifactKind` / `selectProjectArtifacts` |
| `uiSlice` | `artifactDrawerOpen` / `activeArtifactId` / `openArtifact` / `toggleArtifactDrawer` |
| `ArtifactDrawer.tsx` | 右侧抽屉列表 + 摘要 |
| `ArtifactSplitLayout.tsx` | 双栏壳（Wiki 双栏模式） |
| `ActionsPanel` / `DoeResultsPanel` | 双栏接线 |
| `App.tsx` | 顶栏入口 + 挂载抽屉 |

## 测试

- `selectProjectArtifacts` 按 store 字段派生条目
- `ArtifactDrawer` 点击调用 `openArtifact`
- `ArtifactSplitLayout` 渲染左右槽
- `openArtifact` 同步设置 `openModal`
