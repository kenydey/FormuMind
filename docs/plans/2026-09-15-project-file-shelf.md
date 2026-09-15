# 维 4：项目导出货架（Project File Shelf）

> 状态：**已实现**（2026-09-15）  
> 来源：Yuxi 借鉴评估维 4（沙箱 / UserWorkspace）；对照 `vendor/Yuxi` Workspace + sandbox-provisioner  
> 约束：**不**引入 Docker/K8s sandbox、**不**执行任意命令、**不**移植 LangGraph / provisioner  
> 目标：把推荐榜 CSV/JSON、DOE 导出、深度报告等「可下载产物」落到**项目级文件货架**，可在「产物」抽屉内浏览 / 下载 / 删除

## 决策（默认锁定）

1. **落盘位置**：`{db_dir}/project_exports/{project_id}/`（与 `attachments/` 并列），非对象存储、非沙箱挂载
2. **入口**：维 1「产物」抽屉新增页签 **导出货架**（对称活产物列表）
3. **API**：`GET/POST/DELETE /api/projects/{id}/exports` + `GET .../exports/{filename}`；禁止 `..` / 绝对路径
4. **写入点（MVP）**：配方榜「保存到货架」、单卡 CSV/JSON；深度报告 Markdown（若有）
5. **不做**：命令执行、Docker sandbox、Yuxi `/api/workspace` 目录树、跨用户个人空间、Viewer 代理

## Yuxi 只读参照

- `WorkspaceView.vue` / `WorkspaceFileList.vue` / `WorkspacePreviewPane.vue` — 借「列表 + 下载」交互，不借栈
- `docs/mechanisms/sandbox.md` — 明确 **不** 复刻 runtime identity / provisioner

## 测试

- 创建项目后 PUT 文本导出 → list → download → delete
- 路径穿越文件名被拒
- 无项目 → 404
- 前端：货架列表渲染；`saveExportToShelf` API 封装
