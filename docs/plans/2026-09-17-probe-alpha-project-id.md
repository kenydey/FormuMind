# 探针默认 α 跟 settings + 推荐注入 activeProjectId（独立方案 + MVP）

> 状态：**已实现**（2026-09-17）  
> 前置：[#109](https://github.com/kenydey/FormuMind/pull/109) 后端已共享 `kb_hybrid_alpha`，推荐融合读 `req.project_id`  
> 缺口：探针 UI 写死 `alpha=0.3`；同步/异步推荐几乎不把 `activeProjectId` 写入 `requirement.project_id` → 回灌在真机上仍常扫全局  
> 约束：不改 hybrid 公式；不开默认 LLM 精排；不做「应用探针」持久化大 UI

## 0. 结论摘要

| 已有 | 证据 |
|------|------|
| 共享 α | `settings.kb_hybrid_alpha`；probe/`hybrid_search`/`search_chunks_hybrid` |
| 推荐融合项目 | `_fuse_recommend_kb_hybrid(..., req.project_id)` |
| 探针 UI | `RetrievalProbePanel` 本地 `useState(0.3)` |
| 推荐发出 | `researchSlice` 直接传 `requirement`，多数路径无 stamp 项目 |

| 本 MVP 做 | 不做 |
|-----------|------|
| `GET /api/kb/retrieval-settings` 暴露共享旋钮 | 可写 settings / 热更新 α |
| 探针 `active` 时拉默认 α，脚注标明「与推荐共享」 | 每会话「一键应用到生产」 |
| `withActiveProjectId(requirement, activeProjectId)` 注入推荐/研究/AI 修改路径 | 改 Celery worker 契约之外的字段 |

## 1. 决策锁定

1. **只读 settings 端点**：轻量 JSON，不进 `LLMSettings` 大对象  
2. **探针**：面板激活且尚未手动改 α 时，用服务端默认覆盖初始值；用户拖动后不再强制覆盖  
3. **推荐**：若 `requirement.project_id` 已非空则保留；否则填 `activeProjectId`  
4. **覆盖面**：`runResearch` / `runSyncRecommend`（research + recommendFormulations）/ `runAiModifyFormula`

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-17-probe-alpha-project-id.md` | 本方案 |
| `backend/app/api/kb.py` | `GET /retrieval-settings` |
| `frontend/src/api.ts` | 类型 + `kbRetrievalSettings` |
| `frontend/src/utils/withActiveProjectId.ts` | 纯函数 |
| `frontend/src/store/slices/researchSlice.ts` | 三路径注入 |
| `frontend/src/components/knowledge-hub/RetrievalProbePanel.tsx` | 拉默认 α + 脚注 |
| 对应 vitest / pytest | 覆盖 |

## 3. 验证

- `GET /api/kb/retrieval-settings` 含 `kb_hybrid_alpha`  
- `withActiveProjectId`：空 pid 注入；已有 pid 不覆盖  
- 探针激活后 alpha 控件等于服务端值（mock）  
- researchSlice 发出的 requirement 带 `project_id`（单测 store 或纯函数级）  
