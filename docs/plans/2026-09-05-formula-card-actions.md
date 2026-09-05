# 推荐配方卡片操作栏:修改 / AI 修改 / 删除 / 保存(供 DOE)实施计划

日期:2026-09-05 · 状态:待评审 · 代码库:FormuMind(main @ 9928c1b)

## 一、现状摸底(全部实证,含缺口)

| 能力 | 现状 | 位置 |
|---|---|---|
| 组分手动编辑 | ✅ 卡片展开后表格内联编辑已可用(`editable` 恒开) | `RecommendedFormulaTable.tsx` + `updateFormulaIngredient` |
| AI 修改 | ⚠️ 后端已支持**单基准配方**(`baseFormulation`) | `modifyFormulations` API + `runAiModifyFormula(prompt, baseIndex)` |
|  | ⚠️ 但 UI 入口是**全局底部按钮**,恒以 #1 为基准、无提示词预填、结果追加 3 条**新卡**(原卡不动) | `FormulaLeaderboard.tsx` L492-499 |
| 版本化保存 | ✅ `formulation_versions` 表 + lineage/版本链完整 | `formulations.py` POST /formulations/versions |
|  | ⚠️ 入口藏在每卡「🕘 修订历史」弹窗内,无显式保存按钮 | `VersionHistoryModal.tsx` |
| 删除配方 | ❌ **不存在**(store 无 remove action) | `researchSlice.ts` |
| DOE 基准配方 | ⚠️ `requirement.active_formulation` 后端已消费(build_doe_factors 以其为基准算因子) | `workflow.py:267` |
|  | ❌ **前端无任何设置入口**(无 setActiveFormulation,恒为 None → DOE 因子退回 knowledge 兜底基线) | `api.ts:51` 仅声明字段 |

**核心洞察**:后端对「保存供 DOE 调用」已预留消费端(`active_formulation`),但前端从未提供通道。本次 = 前端为主、后端零/极少改动的补齐工程。

## 二、需求 → 方案映射

每个配方卡片底部操作行新增 4 个按钮(**不改变现有导出/组分编辑/IP/历史入口**,叠加式):

| 按钮 | 行为 | 落地 |
|---|---|---|
| ✎ 修改 | 卡片进入「编辑模式」:组分表格聚焦可改 + 卡头显"编辑中"标签;点「完成」退出 | 复用内联编辑;加显式编辑态开关(防误触+提示) |
| 🤖 AI 修改 | 弹出提示词窗口(预填"以当前配方 #{rank} 为基础…"),**仅以此卡为基准**调用已有 `runAiModifyFormula(prompt, baseIndex=i)` → 追加 AI 变体新卡(叠加模式,原卡保留;卡片头现有 `AI修改` 徽标自动带出) | 复用现有弹窗/action,只改触发点传 index |
| 🗑 删除 | 移除该卡(带确认);autosave 持久化 | 新增 `removeFormula(formulaIdx)` |
| 💾 保存(DOE) | ① 写版本库(`saveFormulationVersion`,lineage,change_summary="从推荐列表保存") ② **设为 DOE 基准配方**:`requirement.active_formulation = 该配方` + autosave ③ 成功 toast「已保存,DOE 将围绕此配方设计」 | 新增 `saveFormulaToDoe(formulaIdx)` + 基准指示 |

**DOE 基准语义(验证过链路)**:保存后用户点 DOE 生成 → `build_doe_factors` 取 `req.active_formulation` 的 ingredients 生成 levers→factors → 因子水平围绕该配方实际用量——**真正"供下一步 DOE 调用"**(非另建表另起炉灶)。

## 三、文件变更清单

### 前端(全部新增/修改)
| 文件 | 变更 |
|---|---|
| `store/slices/researchSlice.ts` | +`removeFormula(formulaIdx)`(splice + autosave);+`saveFormulaToDoe(formulaIdx)`(调 api.saveFormulationVersion → set requirement.active_formulation → scheduleAutosave → 返回消息);+`setActiveFormula` 由 save 内联即可 |
| `store/types.ts` | actions 类型 +2 |
| `components/FormulaLeaderboard.tsx` | FormulaCard 底部新增 4 按钮操作行;✎ 编辑态 state(editIdx/编辑中高亮);删除确认(二次点击或 window.confirm——按现有惯例用内联确认);AI 弹窗复用现有 `showAiPrompt`,打开时记录 `aiTargetIdx`,提交传 baseIndex=target;卡片头显示"DOE 基准"徽标(active_formulation 命中时) |
| `components/RecommendedFormulaTable.tsx` | 不改逻辑;编辑态由父级 `editable` 传入控制(现状已支持) |
| 测试 | `researchSlice.test.tsx`(或现 store 测试文件)+`removeFormula/saveFormulaToDoe` 单测;FormulaCard 按钮渲染/删除确认/基准徽标(新建 `FormulaLeaderboard.test.tsx`,参考现有 Modal/Notification 测试风格) |

### 后端(预期极小,标注待验证)
| 文件 | 变更 |
|---|---|
| `app/api/formulations.py` | 无新端点(版本保存已存在)。若前端需要"按名查已保存配方状态"再议——默认不需要 |
| `app/pipeline/workflow.py` / `doe_builder` | **零改动**(active_formulation 消费端已存在) |
| 待验证项 | ① autosave 序列化是否含 `active_formulation`(含 → 重启后基准仍在;不含 → 前端 autosave payload 需补字段,预计在 api.ts submit 层已有全量 requirement 序列化) ② `levers_to_doe_factors` 对推荐配方(含 w/w% 非 100% 归一)的兼容 |

## 四、实施步骤(测试先行,分 3 步提交)

1. **store 层**:removeFormula / saveFormulaToDoe + 类型 → 单测(红色先验证) 
2. **UI 层**:FormulaCard 操作行(修改编辑态 / 删除确认 / AI 基准传参 / 保存) → Vitest + tsc
3. **端到端验证**:推荐一次 → 逐卡 修改(改组分%)→ AI 修改(仅该卡,观察追加) → 删除冗余卡 → 保存(设基准)→ DOE 生成看因子是否围绕保存配方 → git 分 2-3 commit(feat: store actions / feat: 卡片操作栏)

## 五、风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| autosave 不含 active_formulation → 重启丢基准 | 中 | 步骤 0 先验证序列化;缺则前端 save 时一并写 requirement 全量(autosave 现有通道) |
| AI 修改追加变体导致 leaderboard 膨胀 | 低 | 删除按钮兜底;变体卡头已有 AI修改 徽标区分 |
| 内联编辑误触(现无编辑态,任何点击即改) | 低 | ✎ 编辑态收敛:非编辑态下表格改回只读(editable 由卡片编辑态控制——现状 editable 恒开,本次改为编辑态驱动,顺带修复潜在误触) |
| DOE 因子对推荐配方兼容(归一/极值) | 中 | 验证 levers_to_doe_factors 行为;异常则保存时触发一次 validate 兜底提示 |
| 版本保存需 lineage 语义(新配方 vs 分支) | 低 | 无 lineage_id → 后端自动开新链(SaveVersionRequest 已支持 lineage_id=None) |

## 六、不做的事(边界)
- 不做原地"替换"式 AI 修改(保留叠加语义,用户自行删除原卡)
- 不做数据库新表(复用 formulation_versions;DOE 基准走 active_formulation)
- 不改 DOE 后端生成逻辑
