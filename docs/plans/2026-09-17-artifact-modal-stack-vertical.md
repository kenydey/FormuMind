# 产物区 Modal 布局改回纵向（推荐 / DOE）

> 状态：**已实现**（2026-09-17）  
> 反馈：加入产物区后，推荐配方 / DOE 设计 Modal 变为左按钮 + 右内容，右侧拥挤  
> 目标：结果回到功能按钮**下方**全宽展示（对齐改前体验）

## 改动

| 文件 | 说明 |
|------|------|
| `ArtifactSplitLayout.tsx` | `md:grid-cols-2` → `flex-col` 纵向堆叠；`data-layout="stack"` |
| `ArtifactSplitLayout.test.tsx` | 断言 stack，禁止双栏 class |
| `docs/plans/2026-09-15-artifact-workspace.md` | 决策同步为纵向 |

产物抽屉入口、「在产物工作区打开」链接保留；仅改 Modal 内排版。
