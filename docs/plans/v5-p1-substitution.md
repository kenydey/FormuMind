# v5 P1-2 替代推理实施计划

状态：已确认，待实施

## 目标
KG `inhibits` 命中的配方自动提供一键替代，`measured` 候选排首位，Leaderboard 内闭环。

## 证据链（代码锚点）
- `backend/app/services/kg_recommend_score.py:31 kg_compat_adjust`：inhibits 降权 0.5、measured 加成 1.15，已有但未提供替代
- `kg/graph_query.py:151 discover_substitutes`：已实现两跳 substitutes，不区分 measured
- `frontend/.../FormulaLeaderboard.tsx:200` 仅展示 kg_compat，不可操作
- `MaterialSubstitutionModal.tsx:63` 需手动选点，未与 measured/inhibits 联动

## 方案
1. 后端 `discover_substitutes` 增加 `measured` 优先排序：查询候选后，用 `get_kg_feedback_stats` 或 entity metadata 判断是否含实测，measured 前置
2. 前端 `FormulaCard`：当 `kg_compat.feasible==false` 时展示“🔁 一键替代”按钮
   - 点击：提取 `incompatible_pairs` 首个材料名 → 打开 `MaterialSubstitutionModal` 并预填
   - Modal 内 candidates 按 measured 置顶（后端已排，前端保底再排）
3. 复用现有 `substitution` 评估接口，不新增落库流程

## 改动清单
- `backend/app/services/kg/graph_query.py`（排序加成，可选）
- `frontend/src/components/FormulaLeaderboard.tsx`（按钮 + 状态）
- `frontend/src/components/MaterialSubstitutionModal.tsx`（measured 排序透传）
- `frontend/src/store/slices/uiSlice.ts` 或等效打开逻辑（若需透传 initialMaterial）

## 验证
- tsc --noEmit PASS
- 手工：造 inhibits 配方（已知不相容对），Leaderboard 出现一键替代；点击后 measured 候选首位
