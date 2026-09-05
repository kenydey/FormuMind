# DataLab × AG Grid 深度集成方案（DOE 实验工作台 2.0）

> 状态：待评审 | 日期：2026-09-02 | 前序：datalab-platform-maximize.md（P0–P4 已落地）
> 范围：DataLab 能力 + AG Grid 渲染/编辑能力 → FormuMind DOE 工作台（前端风格按 FormuMind 现有设计体系）

## 1. 现状实证与关键约束

### 已集成面（不复述，见 §9 前序文档）
DataLab：样品 CRUD/自定义块/鉴权/Collections/版本读/文件归档读回（5 能力面，后端完整）
AG Grid（LabWorkbench）：行网格编辑/右键菜单(QC/附件/版本/谱系/备注/标签)/CSV 导出/两行对比/状态徽章渲染

### 关键约束（实证）
1. **AG Grid Community v36**（无 enterprise 包）：分组/树、透视/聚合行(aggFunc)、
   master/detail 详情面板、范围选择、Excel 导出**均不可用**——LabWorkbench 现有
   `masterDetail={true}` 是静默无效配置。Community 可用：排序/过滤/多选 checkbox/
   列固定/单元格渲染与类/cellEditor（含下拉）/tooltip/CSV/剪贴板。
2. **平台 Vue UI 不可达**（无 OAuth）→ 一切 DataLab 交互经 API，由 FormuMind 前端呈现。
3. **平台 delete-file API 与存储布局不符**（files.py 按 item_id 目录删，实际按 file_id
   目录存）→ 附件删除需 FormuMind 侧绕行或修平台。
4. **restore-version 契约可用**（POST /items/<refcode>/restore-version/ {version_id}，
   平台保护 refcode/creator/文件字段，version 只增不回退——恢复可再恢复，安全）。

## 2. 目标

把「DOE 行表格」升级为「**实验执行工作台**」：
- 行 = 活的状态机（Pending→Running→Completed），一眼看清每行**该做什么/差什么**
- 测量回填（当前 DataLab 最大空白：28 行空测量模板）成为主操作流：spec 判定、
  校验、批量、回灌状态可见
- 所有 DataLab 深度能力（版本恢复、附件管理、同步状态）在行上下文内直达
- 零 enterprise 依赖；风格沿用现有 dark 面板/色板

## 3. 架构（前端为主，后端薄扩展）

```
┌─ DOE 工作台（WorkbenchModal → LabWorkbench 2.0）────────────────────┐
│ 工具栏：行统计徽章 | 批量操作▾ | CSV 导出 | 训练数据状态            │
│ ┌───────────────────────────────────────────────┬────────────────┐ │
│ │ AG Grid（Community 全部能力）                  │ 行详情侧栏      │ │
│ │  • 测量列：spec 判定着色 + tooltip + 校验编辑  │ （选中行常驻）  │ │
│ │  • status 列：徽章 + 下拉编辑                  │  计划 vs 实际   │ │
│ │  • 版本数/附件数/回灌徽章列                    │  预测 vs 实测   │ │
│ │  • 计划vs实际差异高亮 / dirty 未保存高亮       │  测量健康度     │ │
│ │  • 多选 → 批量标状态/填测量模板/清除           │  →版本/附件/QC  │ │
│ └───────────────────────────────────────────────┴────────────────┘ │
└──────────────┬──────────────────────────────────────────────────────┘
               │ fetch/restore/attach/delete   (现有 api.ts + 新方法)
      ┌────────▼─────────┐   ┌───────────────────────────┐
      │ FormuMind 后端    │──▶│ DataLab API（鉴权 key）    │
      │ experiments.py    │   │ restore-version/upload/   │
      │ + restore/delete  │   │ versions/files/...        │
      └───────────────────┘   └───────────────────────────┘
```

## 4. 方向分级与内容

### P1 行渲染与编辑 2.0（纯前端，Community 能力，无风险）
| 项 | 内容 |
|---|---|
| 测量列 spec 判定 | 值 vs 后端 spec 窗口 → 绿(通过)/红(超)/灰(空)；tooltip 显示 spec 窗口与单位 |
| 测量编辑校验 | 数字编辑器 + 单位后缀 + 非数字拦截（后端同源 objectives spec 驱动） |
| status 下拉 | agSelectCellEditor 枚举（Pending/In Progress/Completed/Blocked）→ 后端合法值 |
| 计划 vs 实际差异 | 实际≠计划 的因子单元格黄色高亮 + tooltip |
| 状态徽章升级 | 现有 StatusCellRenderer 扩展：Running 呼吸色 / Blocked 灰 |
| 徽章列 | 版本数(点击→版本弹窗)、附件数(已有计数，改可点)、「已入训练」✓ |
| dirty 追踪 | 本地编辑行标记（未保存）→ 行左边框 accent 高亮；保存后清除 |

### P2 批量操作 + 行详情侧栏（前端，Community 多选）
- 工具栏批量：标状态 / 清测量 / 复制计划→实际（差异清零）——选 N 行执行，confirm 确认
- **行详情侧栏**（onSelectionChanged 常驻右侧，聚合散落弹窗）：
  计划 vs 实际参数表、测量 vs spec 表（判定色）、预测 vs 实测（预测列值已在行内）、
  快捷入口：🕘版本 / 📎附件 / 📄QC / 🧬谱系 按钮（弹现有 modal）
- 行统计徽章：总行 / Completed / 有测量 / 已入训练（实时）

### P3 版本恢复闭环（前后端）
- 后端：POST /experiments/workbench/{cid}/rows/{rid}/versions/{version_id}/restore
  → 平台 restore-version → 200 后返回（平台自动留新版本，可再恢复）
- 前端：RowVersionHistoryModal 每项加「⏪ 恢复到该版本」（红色确认文案）→ 调用 →
  刷新版本列表 + 通知 workbench 重载行数据（行内容回滚）
- 语义护栏：恢复的是整行 item 快照（含 status/测量）——确认框明示将覆盖当前值

### P4 附件删除/替换（前后端）
- 删除：FormuMind 后端 DELETE 附件端点 = 删本地 attachment+source_document 记录
  （解除绑定）；平台文件走「真删」需修平台 delete-file bug → **默认解除绑定保留平台
  副本**（可追溯），「连平台副本一起删」标 v2（修 /root/datalab files.py 路径 bug 后）
- 替换：上传端点带 replace（平台 replace_file 参数已支持）→ 附件预览「更新文件」

### P5 回灌状态可视化（薄）
- 行「已入训练」判定：experiments 有 label=wb:{cid}:{item_id} 且 measured 非空
  → 后端行响应带 ingested 标志（list_rows 时聚合一次）→ 列徽章 + 侧栏提示
- TrainingDataBanner 联动刷新已在（sync 后）

**明确不做**：AG Grid enterprise 功能（分组/透视/主从）——评估过无 license 不可用；
如未来需要「按状态/因子分组浏览」，用工具栏筛选+排序替代，或单独评估 enterprise 采购。

## 5. 文件变更清单

| 文件 | P1 | P2 | P3 | P4 | P5 |
|---|---|---|---|---|---|
| frontend/src/components/LabWorkbench.tsx（列/渲染/编辑/工具栏/侧栏） | ✅ | ✅ | | | ✅ |
| frontend/src/components/RowDetailSidebar.tsx（新） | | ✅ | | | |
| frontend/src/components/RowVersionHistoryModal.tsx（恢复按钮） | | | ✅ | | |
| frontend/src/components/AttachmentPreview.tsx（删除/替换） | | | | ✅ | |
| frontend/src/api.ts（restore/delete/ingested 类型） | | | ✅ | ✅ | ✅ |
| frontend/src/components/StatusCellRenderer.tsx（扩展） | ✅ | | | | |
| frontend/src/components/SpecCellRenderer.tsx（新，测量判定） | ✅ | | | | |
| backend/app/api/experiments.py（restore/delete 端点 + ingested 标志） | | | ✅ | ✅ | ✅ |
| backend/app/db/campaign_store.py（行响应 ingested） | | | | | ✅ |
| backend/tests/*（restore/delete/ingested 契约测试） | | | ✅ | ✅ | ✅ |

## 6. 实施步骤与时间表

| 步骤 | 内容 | 耗时 | 依赖 |
|---|---|---|---|
| P1.1 | SpecCellRenderer + 测量列 spec 元数据（后端行响应带 spec 窗口？现 workbench 已有 objectives——前端可从 objectives 映射） | 1d | 无 |
| P1.2 | 测量校验编辑 + status 下拉 + 差异高亮 + dirty 追踪 | 0.75d | P1.1 |
| P1.3 | 徽章列（版本数/附件/已入训练占位） | 0.25d | 无 |
| P2.1 | 工具栏统计徽章 + 批量操作（标状态/清测量/复制计划→实际） | 0.5d | P1 |
| P2.2 | 行详情侧栏（参数/测量/spec/预测对比 + 快捷入口） | 1d | P1 |
| P3.1 | 后端 restore 端点 + 测试 | 0.25d | 无 |
| P3.2 | 版本弹窗恢复按钮 + 行重载 | 0.25d | P3.1 |
| P4.1 | 附件删除端点 + 预览删除按钮 | 0.5d | 无 |
| P5.1 | ingested 标志（后端聚合+前端列） | 0.5d | 无 |
| Σ | | ~5.5 天 | |

## 7. 风险矩阵

| 风险 | 概率 | 缓解 |
|---|---|---|
| spec 元数据不一致（前端 objectives vs 后端） | 中 | spec 判定放后端模板（后端 objectives_snapshot 为准），前端只渲染 |
| 恢复操作覆盖当前行数据 | 低 | 平台恢复留新版本（可再恢复）；确认框明示；恢复后行重载 |
| 批量误操作 | 低 | confirm 对话框 + 操作后行状态可见（无 undo——批量前提示） |
| Community 能力误用（如误配 enterprise 特性） | 中 | 方案只列 Community 特性；PR 自查禁 enterprise API |
| 附件删除只解除绑定（平台副本残留） | 低 | UI 文案明示；v2 修平台 delete bug 后支持真删 |
| 行数据大时侧栏重渲染 | 低 | 侧栏仅选中行，数据量小 |

## 8. 验证方式（每 P 交付标准）
- P1：浏览器实测——空测量行显灰、超 spec 显红 tooltip、输错类型拦截、下拉改状态、
  计划≠实际黄色高亮、编辑未保存行 accent 标记
- P2：选中行侧栏信息与行一致；批量标 5 行 Completed 一次保存成功；统计徽章实时
- P3：恢复 v1 → 行内容回到 v1 → 版本列表出现新「restored」版本 → 再恢复可逆
- P4：删除附件 → 列表消失 → 数据库记录解除（平台文件仍在）
- P5：完成+测量行出现「已入训练」✓；横幅计数一致
- 每个 P：相关 pytest 全绿 + 真实 HTTP smoke + 浏览器真实点击（复用 DOE campaign 14）

## 9. 建议
P1→P2 连续（同为 LabWorkbench 前端改动面）；P3/P4/P5 独立小步随时插。合计 ~5.5 天。
不做 enterprise 采购评估；不做平台 UI 复活（OAuth 无凭据）。
