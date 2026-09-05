# 删除 3D 分子视图（MolViewer）预留占位方案

> 状态：待评审 | 日期：2026-09-03 | 类型：前端死代码清理（小改动）

## 1. 实证结论

| 核查项 | 结果 |
|---|---|
| 后端「预留接口」 | **不存在**——无任何 molecular-viewer/3d/structure API 端点或服务。后端 reviewer 类匹配均属审查器（inspector/feasibility，真实功能），与 viewer 无关 |
| 前端占位组件 | `frontend/src/components/MolViewer.tsx`（51 行，纯静态占位「即将上线」，声明 MolEntry 数据契约 + 注释称未来挂 3Dmol.js） |
| 引用面（全量） | 仅 `FormulaLeaderboard.tsx`：L13 import + L230 单行渲染（独立自闭合组件，无容器包裹） |
| 测试引用 | 无（前端无 MolViewer 测试；后端无关联） |
| 数据依赖 | `ingredients[].smiles` 字段为**真实化学核心数据**（KG 图谱/MolJSON/分子描述符/替代推荐在用）——**仅删除渲染占位，smiles 字段与数据流不动** |

## 2. 变更清单

| 文件 | 变更 |
|---|---|
| `frontend/src/components/MolViewer.tsx` | **删除**（git rm） |
| `frontend/src/components/FormulaLeaderboard.tsx` | 删 L13 import + L230 渲染行（两处） |

后端零改动。无新增文件。无迁移。

## 3. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| FormulaLeaderboard 布局受牵连 | 低 | 低 | 渲染行独立自闭合，删除后邻接块（成本/VOC 徽标与按钮行）间距自然闭合 |
| smiles 数据被误连带删除 | 无 | — | 方案明示只删组件与引用；smiles 字段属 API 类型与数据流，不动 |
| 将来想加 3D 视图需重写占位 | 确定 | 极低 | 占位本就无功能；届时按真实需求设计（引入 3Dmol.js 时数据契约按当时 ingredient 结构重新定义） |

## 4. 验证（交付标准）
1. `tsc --noEmit` 0 错误（无残留引用）
2. `vitest run` 相关套件全绿（FormulaLeaderboard 如有测试）
3. vite 转译 200 + 浏览器打开配方推荐/排行榜面板：无「即将上线」占位残留、布局正常
4. 配方成分 SMILES 数据流不受影响（chemical 详情仍返回 smiles）

## 5. 执行
- 单个 commit：`chore(frontend): 移除 3D 分子视图预留占位（MolViewer）——非核心功能`
- push 至 main（默认执行）
- 删除前无需备份（git 历史即回滚）；如需恢复 `git revert` 即可

评审后即执行。
