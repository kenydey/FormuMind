# FormuMind A⁺ 增强方案：全方位 AG Grid + Datalab 深度利用

> **目标**：在方案 A⁺（保留 AG Grid + Datalab）基础上，为每个功能点提供前后端对应的完整开发方案。
>
> **原则**：
> - 所有 AG Grid 增强仅使用 **Community 免费版** 功能（MIT 许可证）
> - 所有 Datalab 增强基于其现有 REST API
> - OneAPI 为 VPS 级代理工具，不计入 FormuMind 依赖
> - 分 Phase 执行，每个 Phase 可独立交付测试

---

## 目录

1. [Phase 0：基础加固（前置，1 天）](#phase-0基础加固前置1-天)
2. [Phase 1：AG Grid 深度交互增强（3-5 天）](#phase-1ag-grid-深度交互增强3-5-天)
3. [Phase 2：Datalab ELN 能力激活（5-7 天）](#phase-2datalab-eln-能力激活5-7-天)
4. [Phase 3：闭环增强 + 可视化（3-5 天）](#phase-3闭环增强--可视化3-5-天)
5. [附录：数据模型变更汇总](#附录数据模型变更汇总)

---

## 优先级总览（Datalab + AG-Grid 最大作用，无冗余）

> **核心原则**：单一职责 + 单一数据流 + 不重复造轮子。
> - **FormuMind** = 计算大脑（配方推荐 / DOE / 贝叶斯优化 / 知识库检索）
> - **Datalab** = 数据 SSOT + **可追溯性**（实验记录 / 附件 / 检测报告 / 谱系）
> - **AG-Grid** = 交互层（高效录入 / 校验 / 快速判断）

| 级别 | 功能 | 理由 |
|------|------|------|
| **S（核心必做）** | 2.6 检测报告归档、2.1 实验附件 | 打通 ELN 可追溯性命脉，Datalab 存在的根本意义 |
| **A（高价值低成本）** | 1.1 条件格式、1.3 范围校验、1.6 Undo/Redo、2.2 实验备注 | 数据质量 + 录入效率，改动最小 |
| **B（中等视规模）** | 1.2 列分组、1.4 Excel 导出、2.3 谱系、3.1 Master/Detail、3.3 历史对比 | 体验提升，实验量增长后再做 |
| **C（冗余/低价值，建议剔除或延后）** | 1.5 侧边栏过滤、2.4 标签、2.5 跨搜索、3.2 Webhook | 与已有能力重复（见下） |

**C 级冗余根因**：
- **3.2 Datalab Webhook** — FormuMind 已有 SSE 任务流做闭环通知，Datalab→FormuMind 反向 webhook 是重复的通知机制。
- **2.4 标签系统** — FormuMind 已有「项目」层级组织，标签是扁平的「第二套组织」，双轨维护成本高。
- **2.5 跨 Campaign 搜索** — FormuMind 已有 KB 检索（ColBERT/BM25），实验数据的全文搜索需求弱，Datalab 搜索是第三套检索。
- **1.5 侧边栏过滤** — 实验量 <50 时列显隐/状态过滤用手点即可，AG Grid 侧边栏属过度设计。

> **一句话结论**：Datalab 只做「可追溯性」（附件 + 检测报告 + 谱系），AG-Grid 只做「高效录入 + 快速判断」（条件格式 + 校验 + Undo），其余让 FormuMind 的计算与检索能力承担——不造第二套标签、第二套搜索、第二套通知。

---

## Phase 0：基础加固（前置，1 天）

### 目标

加固现有 Datalab 链路，确保后续 Phase 有稳定基础。

---

### 0.1 Datalab 自动降级（已具备，仅需配置 + 测试）

**现状**：`get_campaign_store()` 工厂函数已支持 `sqlite` / `datalab` / `auto` 三种模式。
`auto` 模式探测 Datalab 可用性，不可达时静默回退 SQLite。

**变更**：

| 层 | 文件 | 变更 |
|----|------|------|
| 环境变量 | `docker-compose.yml` | 无变更（维持 `datalab`） |
| 后端配置 | `backend/app/config.py:400` | `campaign_backend` 默认值改 `"auto"`（当前为 `"sqlite"`） |
| 后端健康检查 | `backend/app/main.py` | 增加 `/health` 响应中显示当前活跃后端（已有 `campaign_backend` 字段） |

**后端代码变更**：

```python
# backend/app/config.py:400 — 生产环境默认优先 Datalab，不可达自动回退
campaign_backend: str = "auto"  # auto → 探测 Datalab → 回退 sqlite
```

```python
# backend/app/db/campaign_store.py:648 — _resolve_backend() 已有 auto 逻辑
def _resolve_backend(s: Settings) -> str:
    raw = (s.campaign_backend or "auto").lower()
    if raw != "auto":
        return raw
    reachable, _ = check_datalab_reachable(s.datalab_api_url)
    return "datalab" if reachable else "sqlite"
```

**前端变更**：无。

**验证**：
```bash
# 1. Datalab 正常时
curl -s http://127.0.0.1:8000/health | jq '.datalab'
# → {"required": true, "reachable": true, "backend": "datalab"}

# 2. 停止 Datalab 后
docker stop <datalab-container>
curl -s http://127.0.0.1:8000/health | jq '.datalab'
# → {"required": false, "reachable": false, "backend": "sqlite"}
```

---

### 0.2 实验附件后端 API（已有模型，补端点）

**现状**：`ExperimentAttachment` 模型 + `MeasurementStore.attach_in()` 方法已存在，但无 REST 端点。

**新增 API**：

#### `POST /api/experiments/{experiment_id}/attachments`

| 项目 | 内容 |
|------|------|
| 文件 | `backend/app/api/experiments.py` |
| 请求 | `multipart/form-data`: `file` (必填), `kind` (可选, 默认 `"qc_report"`), `note` (可选) |
| 后端逻辑 | ① 调用 Datalab `POST /upload/` 上传文件 → 获得 `source_document_id`；② 调用 `MeasurementStore.attach_in(session, experiment_id, source_document_id, kind, note)` |
| 响应 | `{ "attachment_id": "uuid", "source_document_id": "doc-xxx", "kind": "qc_report", "filename": "SEM_042.png" }` |

**后端伪代码**：
```python
@router.post("/experiments/{experiment_id}/attachments")
async def upload_experiment_attachment(
    experiment_id: int,
    file: UploadFile = File(...),
    kind: str = "qc_report",
    note: str = "",
):
    # 1. 上传到 Datalab
    datalab_resp = await datalab_upload(file)  # httpx POST /upload/
    doc_id = datalab_resp["source_document_id"]
    
    # 2. 建立关联
    store = get_measurement_store()
    attachment_id = store.attach(experiment_id, doc_id, kind=kind, note=note)
    
    return {"attachment_id": attachment_id, "source_document_id": doc_id, ...}
```

#### `GET /api/experiments/{experiment_id}/attachments`

| 项目 | 内容 |
|------|------|
| 响应 | `[{ "id": "uuid", "kind": "qc_report", "note": "", "source_document_id": "doc-xxx", "created_at": "..." }]` |
| 后端逻辑 | 直接调用 `MeasurementStore.attachments_for(experiment_id)` |

---

## Phase 1：AG Grid 深度交互增强（3-5 天）

### 目标

将 AG Grid 从「类 Excel 表格」升级为「实验数据交互中心」。

---

### 1.1 条件格式 — 指标列自动 RAG 着色

**效果**：盐雾 ≥ 500h → 绿色背景，< 200h → 红色背景，中间 → 黄色。根据 `ObjectiveSpec` 的 `direction` + `target_value` 自动判定。

**前端变更**：

| 文件 | 变更 |
|------|------|
| `utils/workbenchColumns.ts` | 指标列 `cellStyle` 改为动态函数，读取 `obj.direction` / `obj.target_value` |
| `components/LabWorkbench.tsx` | 无变更（列定义自动生效） |

**前端代码变更**：
```typescript
// utils/workbenchColumns.ts — 替换静态 cellStyle 为动态函数

function ragStyle(obj: ObjectiveSpec): (params: ValueGetterParams) => { backgroundColor: string; color: string } {
  const target = obj.target_value ?? 0
  const dir = obj.direction
  return (params) => {
    const v = Number(params.value)
    if (Number.isNaN(v)) return {}
    let ok = false
    if (dir === "maximize") ok = v >= target
    else if (dir === "minimize") ok = v <= target  
    else if (dir === "match_target") ok = Math.abs(v - target) / Math.max(Math.abs(target), 1) <= 0.1
    return ok
      ? { backgroundColor: "#dcfce7", color: "#166534" }  // 绿色
      : { backgroundColor: "#fef2f2", color: "#991b1b" }  // 红色
  }
}

// 在 colDef 中应用
cols.push({
  colId: `meas_${obj.metric}`,
  headerName: `实测 ${label}${unitSuffix}`,
  editable: true,
  cellStyle: ragStyle(obj),  // ← 替换原来的 "#fefce8" 静态色
  ...
})
```

**后端变更**：无（`ObjectiveSpec` 已在 API 响应中携带 `direction` / `target_value`）。

---

### 1.2 列分组 — 计划/实际/指标三大列组

**效果**：表头层级化，10+ 列不再平铺。

```
┌───────── 计划参数 ─────────┬───────── 实际参数 ─────────┬───────── 性能指标 ─────────┐
│ 树脂%  │ 固化剂% │ pH     │ 树脂%  │ 固化剂% │ pH     │ 盐雾(h)│ 附着力 │ VOC(g/L)  │
├────────┼────────┼────────┼────────┼────────┼────────┼────────┼────────┼───────────┤
│  32.5  │  8.0   │  3.2   │ [编辑] │ [编辑] │ [编辑] │ [编辑] │ [编辑] │  [编辑]   │
```

**前端变更**：

| 文件 | 变更 |
|------|------|
| `utils/workbenchColumns.ts` | 重构 `buildWorkbenchColumnDefs`，添加 `headerGroupComponent` 包装函数 |

**前端代码变更**：
```typescript
// utils/workbenchColumns.ts

export function buildWorkbenchColumnDefs(
  factorKeys: string[],
  objectives: ObjectiveSpec[],
): ColDef<WorkbenchRow>[] {
  const cols: ColDef<WorkbenchRow>[] = [
    { field: "id", headerName: "ID", pinned: "left", width: 64, editable: false },
    { field: "status", headerName: "状态", width: 96, editable: false,
      cellRenderer: StatusCellRenderer },
  ]

  // 计划参数列 — 归入 "DOE 计划" 列组
  const plannedCols: ColDef<WorkbenchRow>[] = factorKeys.map(key => ({
    colId: `planned_${key}`,
    headerName: key.replace(" (DGEBA)", "").slice(0, 12),
    columnGroupShow: "open",  // ← 折叠时显示在组头
    editable: false,
    valueGetter: (p) => p.data?.planned_params?.[key],
    cellStyle: { backgroundColor: "#f3f4f6", color: "#374151" },
    width: 108,
  }))
  cols.push({
    headerName: "DOE 计划",
    children: plannedCols,
  })

  // 实际参数列 — 归入 "实验实际值" 列组
  const actualCols: ColDef<WorkbenchRow>[] = factorKeys.map(key => ({
    colId: `actual_${key}`,
    headerName: key.replace(" (DGEBA)", "").slice(0, 12),
    editable: true,
    valueGetter: (p) => p.data?.actual_params?.[key],
    valueSetter: (p) => { ... },  // 同现有逻辑
    valueParser: numericParser,
    cellStyle: { backgroundColor: "#eff6ff", color: "#1e40af" },
    width: 108,
  }))
  cols.push({
    headerName: "实验实际值",
    children: actualCols,
  })

  // 指标列 — 归入 "性能指标" 列组
  const measCols: ColDef<WorkbenchRow>[] = objectives.map(obj => ({
    colId: `meas_${obj.metric}`,
    headerName: `${obj.display_name || obj.metric}${obj.unit ? ` (${obj.unit})` : ""}`,
    editable: true,
    cellStyle: ragStyle(obj),
    flex: 1, minWidth: 110,
    ...  // 同现有逻辑
  }))
  cols.push({
    headerName: "性能指标",
    children: measCols,
  })

  return cols
}
```

**后端变更**：无。

---

### 1.3 数值编辑器 + 范围校验

**效果**：配方参数列输入时验证范围，防止录入 `pH=14` 这种明显错误。

**前端变更**：

| 文件 | 变更 |
|------|------|
| `utils/workbenchColumns.ts` | 实际参数列使用 `agNumberCellEditor` + 从 DOEPlan 获取 bounds |
| `components/LabWorkbench.tsx` | 将 `doePlan.factors` 传入 `buildWorkbenchColumnDefs` |

**前端代码变更**：
```typescript
// utils/workbenchColumns.ts
// 函数签名增加 doePlan 参数
export function buildWorkbenchColumnDefs(
  factorKeys: string[],
  objectives: ObjectiveSpec[],
  doePlan: DOEPlan,           // ← 新增
): ColDef<WorkbenchRow>[] {

  const factorMap = new Map(doePlan.factors.map(f => [f.name, f]))

  // 实际参数列
  const actualCols: ColDef<WorkbenchRow>[] = factorKeys.map(key => {
    const factor = factorMap.get(key)
    return {
      colId: `actual_${key}`,
      headerName: key.replace(" (DGEBA)", "").slice(0, 12),
      editable: true,
      cellEditor: "agNumberCellEditor",                    // ← 新增
      cellEditorParams: {
        min: factor?.low,                                   // ← 从 DOE Plan 获取
        max: factor?.high,
        precision: 2,
        showStepperButtons: false,
      },
      ...  // 同现有
    }
  })
  ...
}

// LabWorkbench.tsx — 传入 doePlan
const columnDefs = useMemo<ColDef<WorkbenchRow>[]>(
  () => buildWorkbenchColumnDefs(factorKeys, objectives, doePlan), // ← 新增参数
  [factorKeys, objectives, doePlan]
)
```

**后端变更**：无（`DOEPlan.factors` 已在 API 响应中携带 `low` / `high`）。

---

### 1.4 Excel 一键导出

**效果**：工具栏增加「导出 Excel」按钮，导出当前台账为 `.xlsx`，保留列分组、条件格式、列宽。

**前端变更**：

| 文件 | 变更 |
|------|------|
| `components/LabWorkbench.tsx` | 工具栏区增加导出按钮 + 处理函数 |

**前端代码变更**：
```typescript
// LabWorkbench.tsx — 新增导出按钮

const handleExportExcel = useCallback(() => {
  gridRef.current?.api.exportDataAsExcel({
    fileName: `DOE-${campaignId}-${new Date().toISOString().slice(0, 10)}.xlsx`,
    sheetName: "实验台账",
    columnKeys: columnDefs.map(c => c.colId || c.field).filter(Boolean),
    processCellCallback: (params) => params.value ?? "",
  })
}, [campaignId, columnDefs])

// 工具栏区（现有 buttons 旁边）
<button
  type="button"
  onClick={handleExportExcel}
  className="text-xs border border-edge rounded px-2 py-1.5 hover:bg-ink/30"
>
  📥 导出 Excel
</button>
```

**后端变更**：无。

**依赖变更**：无额外依赖（`exportDataAsExcel` 是 AG Grid Community 内置功能）。

---

### 1.5 侧边栏过滤器（C 级 · 实验量 <50 可延后）

**效果**：AG Grid 右侧增加侧边栏，可：
- 按状态列过滤（Completed / Pending）
- 控制列显隐
- 实验数 > 50 时自然需要

**前端变更**：

| 文件 | 变更 |
|------|------|
| `components/LabWorkbench.tsx` | `<AgGridReact>` 增加 `sideBar` prop |

**前端代码变更**：
```typescript
// LabWorkbench.tsx — <AgGridReact> 增加
<AgGridReact<WorkbenchRow>
  ref={gridRef}
  theme="legacy"
  rowData={rows}
  columnDefs={columnDefs}
  defaultColDef={defaultColDef}
  sideBar={{
    toolPanels: [
      {
        id: "columns",
        labelDefault: "列显隐",
        toolPanel: "agColumnsToolPanel",
        toolPanelParams: { suppressColumnMove: true, suppressColumnSelectAll: true },
      },
      {
        id: "filters",
        labelDefault: "过滤",
        toolPanel: "agFiltersToolPanel",
      },
    ],
    defaultToolPanel: "filters",
  }}
  ...  // 其余现有 props
/>
```

**后端变更**：无。

---

### 1.6 Undo/Redo 编辑保护

**效果**：Ctrl+Z 撤销误操作，降低数据录入焦虑。

**前端变更**：

| 文件 | 变更 |
|------|------|
| `components/LabWorkbench.tsx` | `<AgGridReact>` 增加 2 个 prop |

**前端代码变更**：
```typescript
// <AgGridReact> 增加
undoRedoCellEditing: true,
undoRedoCellEditingLimit: 20,
```

**后端变更**：无。

---

## Phase 2：Datalab ELN 能力激活（5-7 天）

### 目标

让 Datalab 从「JSON 存储代理」升级为「电子实验记录本（ELN）」，激活其原生能力。

---

### 2.1 实验附件 — 文件上传 + 关联 ⭐（S 级核心，与 2.6 共享 upload_file 链路）

**效果**：右击实验行 → 「上传附件」→ 文件上传到 Datalab → 关联到 Sample。

**前端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api.ts` | 新增 `uploadAttachment()` + `getAttachments()` API 方法 | +20 行 |
| `api.ts` | `WorkbenchRow` 接口增加 `attachments?: Attachment[]` | +8 行 |
| `components/LabWorkbench.tsx` | 增加右键菜单 → 「上传附件」 | +60 行 |
| 新建 `components/AttachmentPreview.tsx` | 附件预览浮层（图片/PDF） | ~80 行 |
| `utils/workbenchColumns.ts` | 「附件」列显示附件数徽章 | +15 行 |

**前端 TypeScript 类型**：
```typescript
// api.ts
export interface Attachment {
  id: string
  kind: string           // "qc_report" | "microscope" | "spectrum" | "photo"
  note: string
  source_document_id: string
  filename: string
  created_at: string
}

// WorkbenchRow 增加
export interface WorkbenchRow {
  id: number
  ...
  attachments: Attachment[]  // ← 新增
}
```

**前端右键菜单**：
```typescript
// LabWorkbench.tsx — 使用 AG Grid getContextMenuItems 回调

const getContextMenuItems = useCallback((params: GetContextMenuItemsParams) => {
  const rowId = params.node?.data?.id
  if (!rowId) return ["copy", "paste", "separator", "export"]

  return [
    "copy", "paste", "separator",
    {
      name: "📎 上传附件",
      action: () => openAttachmentUploader(rowId),
    },
    {
      name: "🖼️ 查看附件 (" + (params.node?.data?.attachments?.length || 0) + ")",
      action: () => openAttachmentPreview(rowId),
      disabled: !params.node?.data?.attachments?.length,
    },
    {
      name: "✏️ 添加备注",
      action: () => openNoteEditor(rowId),
    },
    "separator",
    "export",
  ]
}, [])

// <AgGridReact> 增加
getContextMenuItems={getContextMenuItems}
```

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api/experiments.py` | `POST /experiments/{id}/attachments` + `GET /experiments/{id}/attachments` | ~50 行 |
| `db/measurement_store.py` | 新增 `with_attachments()` 方法，join `ExperimentAttachment` 表 | ~20 行 |
| `db/campaign_store.py` | `list_rows()` 返回结果中附带 attachments 字段（可选 join） | ~30 行 |

**Datalab API 调用**：
```
Phase 0.2 的附件上传 API 自动对接 Datalab POST /upload/
```

---

### 2.2 实验备注 — 富文本 / 纯文本评论

**效果**：右键实验行 → 「添加备注」→ 弹出文本框 → 保存到 Datalab rich text block。

**前端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api.ts` | `WorkbenchRow` 增加 `note?: string` | +2 行 |
| `api.ts` | `syncWorkbench()` → `BatchUpdateRequest.row` 增加 `note` | +2 行 |
| 新建 `components/NoteEditor.tsx` | 简单 `<textarea>` 弹窗 | ~50 行 |
| `components/LabWorkbench.tsx` | 右键菜单整合 | 见 2.1 |

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api/experiments.py` | `GridRowUpdate` 增加 `note: str \| None` | +2 行 |
| `db/campaign_store.py` | `batch_sync()` 将 note 写入 Datalab `formumind_note` comment block | ~15 行 |
| `db/campaign_types.py` | `WorkbenchRow` 增加 `note: str \| None` | +1 行 |

**Datalab 存储**：
```json
// Datalab sample 新增一个 comment block
{
  "blocks_obj": {
    "formumind_params": { ... },
    "formumind_measurements": { ... },
    "formumind_note": {           // ← 新增
      "blocktype": "comment",
      "data": { "text": "该批次固化温度异常偏高（82°C vs 计划 75°C），已标记待复现" }
    }
  }
}
```

---

### 2.3 样本谱系 — 父子关系追踪

**效果**：当 DOE 闭环生成新配方时，自动建立 `parent_sample_id` 关系。在 AG Grid 中右击 → 「查看谱系」展示配方迭代链。

**前端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api.ts` | `WorkbenchRow` 增加 `parent_sample_id?: string`, `parent_campaign_id?: number` | +3 行 |
| 新建 `components/LineageTree.tsx` | 树形展示迭代链（用 Recharts 或纯 DOM） | ~100 行 |
| `components/LabWorkbench.tsx` | 右键菜单 + 「查看谱系」 | 见 2.1 |

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `db/campaign_store.py` | `create_from_plan()` 时，若 loop 迭代则传 `parent_item_id` 到 Datalab `POST /new-sample/` | ~15 行 |
| `api/experiments.py` | `create_workbench_campaign()` 接受 `parent_campaign_id` | ~10 行 |
| `api/experiments.py` | `GET /experiments/{id}/lineage` 返回谱系链 | ~30 行 |

**Datalab 存储**：
```json
// Datalab POST /new-sample/
{
  "new_sample_data": {
    "item_id": "EXP-042-B",
    "parent_sample_id": "EXP-042-A",   // ← 新增
    "blocks_obj": { ... }
  }
}
```

---

### 2.4 标签系统（C 级冗余 · 与「项目」组织重复）

**效果**：右键实验行 → 「标记」→ 选择/自定义标签 → 同步到 Datalab。

**前端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api.ts` | `WorkbenchRow` 增加 `tags?: string[]` | +2 行 |
| 新建 `components/TagPicker.tsx` | 标签选择器（预设 + 自定义） | ~80 行 |
| `components/LabWorkbench.tsx` | 右键菜单 + 标签徽章渲染（在 StatusBadge 旁） | ~30 行 |

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api/experiments.py` | `PUT /experiments/{id}/tags` 端点 | ~25 行 |
| `db/campaign_store.py` | 标签写入 Datalab block + `campaign_tags` 索引表 | ~40 行 |
| `db/campaign_types.py` | `WorkbenchRow` 增加 `tags: list[str]` | +1 行 |

---

### 2.5 跨 Campaign 搜索（C 级冗余 · FormuMind 已有 KB 检索）

**效果**：搜索页增加「实验数据」tab → 输入「盐雾 > 500 环氧」→ 查询 Datalab 全文搜索 → 返回匹配的 Sample 列表。

**前端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| 新建 `components/ExperimentSearchPanel.tsx` | 搜索栏 + 结果表格 | ~120 行 |

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api/experiments.py` | `GET /experiments/search?q=...` → 透传 Datalab `GET /search/?q=` | ~30 行 |
| `db/datalab_client.py` | 新增 `search(query)` 方法 | ~25 行 |

**Datalab API 调用**：
```
GET /search/?q=盐雾+500+环氧  →  [{ "item_id": "EXP-042", "blocks_obj": {...}, ...}, ...]
```

---

### 2.6 检测报告 → Datalab 归档 ⭐（Phase 2 最高优先级 · 打通 QC 报告与实验台账）

**现状**：右栏「📄 检测报告」按钮（`QCReportModal.tsx` → `POST /qc/report`）已实现「上传 PDF/Word/图片 → 提取带单位/方法/规格限的计量项 → 绑定实验 → 同步进可训练数据」。但报告原始文件与计量项**只存 FormuMind 自己的 sqlite**（`source_documents` + `measurements` 表），与存 Datalab 的实验台账（campaign）**割裂**——在 Datalab/ELN 侧看不到检测报告，追溯链断裂。

**方案**：在 `POST /qc/report` 入库流程中**追加 Datalab 归档**（复用 2.1 附件链路），让「实验记录 + 检测报告」在 ELN 侧统一可追溯。

**数据流**：
```
检测报告文件 → parse_document（解析 + LLM 提取计量项）
   ├─ 原始文件   → Datalab POST /upload/（media block）→ source_document_id
   ├─ 计量项     → 写实验 sample 的 formumind_qc comment block（{metric, value, unit, method, spec_min/max, passed}）
   └─ 关联       → MeasurementStore.attach(experiment_id, source_document_id, kind="qc_report")
```

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api/qc.py` | `ingest_qc_report` 在 `ingest_qc_report_tx` 成功后，追加「上传原始文件到 Datalab + 写计量项 block + attach」；Datalab 不可达时静默降级（保留 sqlite 落库，不阻断报告入库） | ~40 行 |
| `db/datalab_client.py` | 新增 `upload_file(content, filename)` → `POST /upload/` 返回 `source_document_id` | ~25 行 |
| `db/measurement_store.py` | 复用已有 `attach()` / `attachments_for()`（2.1 已规划） | 0 行 |

**Datalab 存储**（实验 sample 新增 block + 文件附件）：
```json
{
  "blocks_obj": {
    "formumind_params": { "..." : "..." },
    "formumind_measurements": { "..." : "..." },
    "formumind_qc": {
      "blocktype": "comment",
      "data": {
        "source_document_id": "doc-xxx",
        "measurements": [
          {"metric": "盐雾", "value": 720, "unit": "h", "test_method": "GB/T 10125", "spec_min": 500, "spec_max": null, "passed": true}
        ]
      }
    }
  },
  "files": [{ "source_document_id": "doc-xxx", "filename": "盐雾报告_042.pdf", "kind": "qc_report" }]
}
```

**前端变更**：无（检测报告入口与展示已实现，归档对前端透明）。

**关键设计点**：
1. **降级优先**：Datalab 归档失败时**不阻断**报告入库——报告仍落 sqlite 并正常解析/绑定，Datalab 归档作为「尽力而为」的后台补充（与现有 `auto` 降级策略一致）。
2. **复用而非重造**：原始文件走 2.1 的 `upload_file` 链路，附件关联走 `MeasurementStore.attach()`，与「实验附件」共用同一套 `source_document_id` 体系，避免「检测报告」和「通用附件」两套存储割裂。

---

## Phase 3：闭环增强 + 可视化（3-5 天）

### 目标

利用 AG Grid Master/Detail + 现有 Recharts 图表库，实现闭环迭代的深度可视化。

---

### 3.1 AG Grid Master/Detail — 展开行查看历史

**效果**：每个实验行可以展开，显示该配方在多轮闭环中的测量值趋势迷你图。

**前端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `components/LabWorkbench.tsx` | `masterDetail={true}` + `detailCellRenderer` | ~80 行 |
| 新建 `components/ExperimentDetail.tsx` | 展开面板：历史趋势图 + 附件列表 + 备注 | ~120 行 |

**前端代码变更**：
```typescript
// LabWorkbench.tsx — 使用 Recharts（已安装）画迷你图

import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts"

function ExperimentDetail({ data }: { data: WorkbenchRow }) {
  return (
    <div className="flex gap-4 p-3">
      <div className="flex-1">
        <h4 className="text-xs font-semibold mb-2">指标历史趋势</h4>
        <ResponsiveContainer width="100%" height={120}>
          <LineChart data={data.measurement_history}>
            <XAxis dataKey="round" hide />
            <YAxis width={30} tick={{ fontSize: 9 }} />
            <Tooltip />
            {objectives.map(o => (
              <Line key={o.metric} dataKey={o.metric} stroke={colorForMetric(o.metric)} dot />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="w-48 text-xs">
        <h4 className="font-semibold mb-1">附件 ({data.attachments?.length || 0})</h4>
        {data.attachments?.map(a => <AttachmentThumb key={a.id} {...a} />)}
      </div>
      <div className="w-48 text-xs">
        <h4 className="font-semibold mb-1">备注</h4>
        <p className="text-slate-400">{data.note || "—"}</p>
      </div>
    </div>
  )
}

// <AgGridReact> 增加
masterDetail={true}
detailCellRenderer={ExperimentDetail}
detailRowHeight={200}
```

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `db/campaign_store.py` | `list_rows()` 附带 `measurement_history: list[dict]`（多轮聚合） | ~30 行 |

---

### 3.2 Datalab Webhook — 闭环收敛通知（C 级冗余 · SSE 任务流已覆盖）

**效果**：闭环收敛后，Datalab 回调 FormuMind → 前端推送通知「第 N 轮 DOE 已收敛」。

**后端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `api/experiments.py` | `POST /experiments/hooks/convergence` 接收 Datalab webhook | ~30 行 |
| `services/workbench_loop.py` | `dispatch_loop_after_sync()` 收敛时调用外部通知 | ~20 行 |

**Datalab 配置**：
```
# 在 Datalab 中注册 webhook
POST /webhooks/register  { "event": "sample.updated", "url": "http://formumind:8000/api/experiments/hooks/convergence" }
```

---

### 3.3 历史版本对比

**效果**：AG Grid 中选中两行 → 「对比」按钮 → 并排展示配方差异。

**前端变更**：

| 文件 | 变更 | 工作量 |
|------|------|--------|
| `components/LabWorkbench.tsx` | `rowSelection="multiple"` + 对比按钮 | ~50 行 |
| 新建 `components/ExperimentDiff.tsx` | 并排差异视图（使用 AG Grid API 或纯 DOM） | ~100 行 |

**后端变更**：无（前端纯数据对比，差异高亮在前端计算）。

---

## 附录：数据模型变更汇总

### WorkbenchRow（前端 + 后端类型同步）

```typescript
// 变更前
interface WorkbenchRow {
  id: number
  campaign_id: number
  status: string
  planned_params: Record<string, number>
  actual_params: Record<string, number>
  measurements: Record<string, number | string>
}

// 变更后
interface WorkbenchRow {
  id: number
  campaign_id: number
  status: string
  planned_params: Record<string, number>
  actual_params: Record<string, number>
  measurements: Record<string, number | string>
  // ── Phase 2 新增 ──
  attachments: Attachment[]              // 文件附件
  note: string | null                    // 实验备注
  tags: string[]                         // 标签
  parent_sample_id: string | null        // 父样本 ID
  parent_campaign_id: number | null      // 父 Campaign ID
  // ── Phase 3 新增 ──
  measurement_history: RoundSnapshot[]   // 多轮历史
}
```

### 新增 API 端点

| 方法 | 路径 | Phase |
|------|------|-------|
| `POST` | `/api/experiments/{id}/attachments` | 0.2 |
| `GET` | `/api/experiments/{id}/attachments` | 0.2 |
| `PUT` | `/api/experiments/{id}/tags` | 2.4 |
| `GET` | `/api/experiments/{id}/lineage` | 2.3 |
| `GET` | `/api/experiments/search?q=` | 2.5 |
| `POST` | `/api/experiments/hooks/convergence` | 3.2 |

### 新增文件

| 文件 | Phase | 作用 |
|------|-------|------|
| `frontend/src/components/AttachmentPreview.tsx` | 2.1 | 附件预览浮层 |
| `frontend/src/components/NoteEditor.tsx` | 2.2 | 备注编辑弹窗 |
| `frontend/src/components/TagPicker.tsx` | 2.4 | 标签选择器 |
| `frontend/src/components/ExperimentSearchPanel.tsx` | 2.5 | 跨 Campaign 搜索面板 |
| `frontend/src/components/ExperimentDetail.tsx` | 3.1 | Master/Detail 展开面板 |
| `frontend/src/components/LineageTree.tsx` | 2.3 | 谱系树形视图 |
| `frontend/src/components/ExperimentDiff.tsx` | 3.3 | 版本对比视图 |

### 总工作量估算

| Phase | 工期 | 前端 | 后端 | 新增文件 |
|-------|------|------|------|---------|
| Phase 0 | 1 天 | 0 行 | ~60 行 | 0 |
| Phase 1 | 3-5 天 | ~200 行 | 0 行 | 0 |
| Phase 2 | 5-7 天 | ~500 行 | ~315 行 | 5 |
| Phase 3 | 3-5 天 | ~300 行 | ~80 行 | 2 |
| **合计** | **12-18 天** | **~1000 行** | **~390 行** | **7** |

---

## 精简版执行路线（只保留 S+A 级 · 可直接开工）

> 按「优先级总览」砍掉 B/C 级后的可交付清单。依赖驱动，每步独立可验证。

### 执行顺序

| Step | 功能 | 依赖 | 前端 | 后端 | 工期 |
|------|------|------|------|------|------|
| **0 基础设施** | `datalab_client.upload_file()` + 0.2 附件 API（POST/GET `/experiments/{id}/attachments`） | 无 | 0 | ~75 行 | 0.5 天 |
| **1 S 级核心** | 2.6 检测报告归档 ⭐（复用 upload_file + attach） | Step 0 | 0 | ~65 行 | 0.5 天 |
| **1 S 级核心** | 2.1 实验附件前端（右键上传 + 列徽章 + 预览浮层） | Step 0 | ~183 行 | ~100 行 | 1 天 |
| **2 A 级** | 1.1 条件格式 RAG 着色 | 无 | ~20 行 | 0 | 0.25 天 |
| **2 A 级** | 1.3 数值编辑器 + 范围校验 | 无 | ~30 行 | 0 | 0.25 天 |
| **2 A 级** | 1.6 Undo/Redo | 无 | 2 行 | 0 | 0.1 天 |
| **2 A 级** | 2.2 实验备注（右键 + formumind_note block） | 无 | ~54 行 | ~18 行 | 0.5 天 |
| **合计** | — | — | **~290 行** | **~258 行** | **~3.5 天** |

### 交付里程碑

1. **M1（Step 0+1）**：检测报告与实验台账在 ELN 侧打通——报告文件 + 计量项归档到 Datalab sample，附件可在台账右键上传/预览。**这是可追溯性命脉，完成后即具备 ELN 核心价值。**
2. **M2（Step 2）**：实验录入体验闭环——条件格式快速判合格、范围校验防录错、Undo 防误操作、备注留痕。

### 与全量方案对比

| 项 | 全量（含 B/C） | 精简（S+A） |
|----|---------------|-------------|
| 工期 | 12-18 天 | **~3.5 天** |
| 代码量 | ~1390 行 | **~550 行** |
| 新增文件 | 7 个 | **2 个**（AttachmentPreview / NoteEditor） |
| 覆盖价值 | 100%（含 4 个冗余） | **核心 100%**（可追溯性 + 录入体验） |
