# 维 5：配方行动技能坞（Action Skills Dock）

> 状态：**已实现**（2026-09-15）  
> 来源：Yuxi 借鉴评估维 5（Skills / Tools / State Panel）；对照 `vendor/Yuxi` skills-management、tools-system  
> 约束：**不**引入 LangGraph / MCP / 子智能体线程；**不**把 ActionsPanel 收成 Agent Chat  
> 目标：用静态涂料 playbook 预设并打开现有 Actions（推荐 / DOE / 寻优 / 闭环 / 深度研究），右侧技能坞展示「活跃技能 · 工具白名单 · 清单」

## 决策（默认锁定）

1. **形态**：Actions 栏顶部 **技能坞**；点击技能 = 应用预设 + 打开对应 Modal（深度研究提示中栏）
2. **技能包**：仓库内静态 JSON/表（3–5 条），`GET /api/formulation-skills` 只读下发；无远程安装、无 Agent CRUD
3. **运行时**：不新增 Celery 图；可选在 progress 上打 `skill_id` 标签（MVP 可仅前端记住 `activeSkillId`）
4. **坞内容**：活跃技能名、工具白名单、清单（复用 `taskThinking` / 阶段 id）
5. **不做**：MCP、ChemMCP、HumanApproval、子代理、MEMORY.md、Yuxi Extensions 市场、Vue 移植

## Yuxi 只读参照

- `docs/agents/skills-management.md`、`docs/agents/tools-system.md`
- 状态面板分区叙事（活跃技能 / 工具 / 待办）— 借信息架构，不借栈

## 测试

- API 返回 ≥3 条技能且含 `action` / `tools` / `checklist`
- 前端坞渲染；选择技能设置 `activeSkillId` 并打开 Modal
- 忙碌时 checklist 与 thinking 对齐（有则高亮）
