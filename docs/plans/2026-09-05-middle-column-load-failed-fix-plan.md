# 中栏「Load failed」排查结论与修复计划

> 日期: 2026-09-05  
> 分支基线: `main`（含 P0–P3 合并后）  
> 范围: **只定案 + 分波修复计划**；本文不实施代码  
> 证据: 本地复现日志、`task_outbox` 查询、浏览器中栏截图、清空 outbox A/B 对照

---

## 0. 执行摘要

| 项 | 结论 |
|----|------|
| **用户可见症状** | 中栏（`ResearchPanel` → `NotificationStack`）顶部错误条；文案可能是 `Load failed` / `Failed to fetch` / `/api/projects -> 500` |
| **直接触发** | `App` 挂载时 `initProjects()` → `GET /api/projects` 失败 → `store.error = formatApiError(e)` |
| **后端根因 (P0)** | lifespan 里**同步** `recover_stalled()`：在 `FORMUMIND_CELERY_EAGER=true` 时把积压 outbox 任务整段跑完，**阻塞「Application startup complete」**，`:8000` 长时间不监听 |
| **次生路径** | `CELERY_EAGER=false` 且无 Redis 时，`.delay()` 卡在 Redis 重试，同样阻塞启动 |
| **前端 UX 根因 (P1)** | 仅对含 `"fetch"` 的错误展示「请确认后端已启动」；WebKit 的 `Load failed` 与代理 `-> 500` 均无行动指引 |
| **非根因** | 中栏业务组件自身逻辑；`OrganizationDashboard` 的「加载失败」文案（非中栏主路径） |

**一句话**：不是「中栏自己 load 挂了」，而是 **API 在启动阶段被 outbox 恢复拖死 → 项目列表请求失败 → 错误泡到中栏通知栈**。

---

## 1. 证据链

### 1.1 UI 路径

```
App.useEffect → initProjects()
  → api.listProjects()  // GET /api/projects
  → catch → draft.error = formatApiError(e)
ResearchPanel → NotificationStack
  → deriveNotifications(error) → 中栏红条
```

关键文件：

- `frontend/src/App.tsx`（挂载初始化）
- `frontend/src/store/slices/projectSlice.ts`（`initProjects` catch）
- `frontend/src/api.ts`（`formatApiError` 原样返回 `Error.message`）
- `frontend/src/components/NotificationStack.tsx`（`error.includes("fetch")` 才给 uvicorn 提示）
- `frontend/vite.config.ts`（`/api` → `127.0.0.1:8000`）

### 1.2 运行时复现（本环境）

| 步骤 | 结果 |
|------|------|
| Vite `:5173` 起、uvicorn 未就绪 | `GET /api/projects` 经代理 → **HTTP 500、空 body** |
| Chromium 打开 SPA | 中栏红条文案：**`/api/projects -> 500`**（截图见 artifacts） |
| 默认 `.env`：`FORMUMIND_CELERY_EAGER=true` | uvicorn 卡在 `Waiting for application startup.`，日志出现 offline recommend / PubChem |
| `task_outbox` | **7** 条 PENDING：`research_recommend×3`、`research_deep×2`、`inverse_design×2` |
| 将 7 条标为 `DEAD` 后同配置重启 | **数秒内** `Application startup complete`；`/health`、`/api/projects` → **200** |

对照实验否证了「单纯 ColBERT / enrich」为主因：`FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP=1` 仍会阻塞——因为 **outbox 恢复不在 skip 分支内**。

### 1.3 文案差异说明（为何有人看到 Load failed）

| 浏览器 / 传输 | `formatApiError` 常见文案 | 是否命中 `includes("fetch")` 提示 |
|---------------|---------------------------|-------------------------------------|
| WebKit / Safari 网络失败 | `Load failed` | ❌ |
| Chrome 网络失败 | `Failed to fetch` | ✅ |
| Firefox | `NetworkError when attempting to fetch resource.` | ✅（含 fetch） |
| Vite 代理后端不可达 | `/api/projects -> 500` | ❌ |

用户口述「load failed」与 Chromium 下的 `-> 500` **同属一类故障**；根因一致，仅错误字符串不同。

### 1.4 代码与注释矛盾

`backend/app/main.py` lifespan 注释写明：

> Recover stalled outbox rows (**best-effort, must not block startup**).

实现却是在 `yield` **之前**同步调用 `dispatcher.recover_stalled(session)` → `_dispatch` → Celery `.delay()`：

- **eager=true**：任务体在启动线程同步执行（本次复现主路径）
- **eager=false + broker 不可用**：连接重试长时间占用启动路径（次生路径）

---

## 2. 根因分层

```
[P0 启动阻塞] recover_stalled 同步 + Celery eager/无 Redis
        ↓
[:8000 未监听]
        ↓
[Vite proxy 500 / fetch TypeError]
        ↓
[initProjects → store.error]
        ↓
[P1 UX] 中栏只显示生硬英文/状态码，WebKit/500 无「后端未就绪」提示
```

次要放大器：

- 本地 `.env` 持久化了 `FORMUMIND_CELERY_EAGER=true`（开发便利，与积压 outbox 组合致命）
- outbox 行 `attempt_count` 仍低，每次重启都会再捞起来跑
- 启动期无「仍在 bootstrap」的 readiness 信号，前端无法区分「正在恢复」与「彻底挂了」

---

## 3. 修复计划（分波）

### Wave A — P0 启动永不被 outbox 拖死（后端，优先）

**目标**: uvicorn 在秒级进入 `Application startup complete` 并接受 `/health`、`/api/projects`；outbox 恢复改为真正 best-effort。

| ID | 改动 | 说明 |
|----|------|------|
| A1 | **lifespan 外移恢复** | `recover_stalled` 放到 `asyncio.create_task` / daemon 线程，在 `yield` **之后**执行；启动路径只做「可失败的调度」 |
| A2 | **eager 短路** | 若 `celery_eager`：恢复路径 **禁止** `.delay()` 同步跑业务；改为标记 `PENDING` 留待显式 worker、或限时入队后立刻返回，并打 warning |
| A3 | **broker 发布超时** | non-eager 时对 `.delay()` 设短超时（如 2–5s）；失败记日志、行回退 `PENDING`，**不**阻塞 lifespan |
| A4 | **恢复预算** | 单次启动最多恢复 N 条 / 总 wall-time 上限；超额留待下一轮或管理 API |
| A5 | **回归测试** | 插入多条假 outbox + eager=true，断言 TestClient/`/health` 在超时阈值内可用；断言恢复在后台发生或不在 eager 下同步执行 |

**验收**

1. 故意插入 ≥3 条 `research_recommend` PENDING + `CELERY_EAGER=true`，冷启动后 **≤5s** `/health` 200  
2. 无 Redis + `CELERY_EAGER=false`，冷启动同样 ≤5s `/health` 200  
3. 现有 outbox / dispatch 单测不回退  

**预估**: 0.5–1 人日  

**回滚**: 恢复为同步调用即可；风险低。

---

### Wave B — P1 中栏错误可读、可行动（前端）

**目标**: 任意浏览器下，后端不可达时中栏给出统一中文说明 + 启动命令，而不是生硬的 `Load failed` / `-> 500`。

| ID | 改动 | 说明 |
|----|------|------|
| B1 | **`formatApiError` 网络归一化** | 识别 `Load failed` / `Failed to fetch` / `NetworkError` / `ECONNREFUSED` / 代理空 body 的 `-> 5xx`，映射为稳定文案，例如：`后端不可达（无法连接 API）。请确认 uvicorn 已在 :8000 启动。` |
| B2 | **NotificationStack 提示条件** | 将 `includes("fetch")` 扩展为「网络类错误」检测（与 B1 共用 helper，如 `isBackendUnreachableError`） |
| B3 | **initProjects 降级策略（可选）** | 启动失败时除 error 条外，允许空项目壳继续操作；或显示「重试加载项目」按钮，避免整栏像「永久坏了」 |
| B4 | **单测** | NotificationStack：对 `Load failed`、`/api/projects -> 500` 均出现 uvicorn 提示；`formatApiError` 单测覆盖上述字符串 |

**验收**: 停掉后端、刷新 SPA → 中栏中文提示含启动命令；Chrome /（如可）WebKit 文案一致。  

**预估**: 0.5 人日  

---

### Wave C — P2 运维与本地 DX

| ID | 改动 | 说明 |
|----|------|------|
| C1 | **`.env.example` / USER_GUIDE** | 明确：`CELERY_EAGER=true` 仅适合无 Redis 的轻量开发；若本地 DB 有积压 outbox，必须配合 A2 或定期清理 |
| C2 | **Settings / EnvFlags 文案** | `celery_eager` 增加「会在启动时同步重放积压任务」风险说明（A2 落地后可改为「启动期跳过同步重放」） |
| C3 | **管理接口或 CLI** | `POST /api/admin/outbox/requeue` / `dead-letter`；或 `python -m app.tools.outbox_gc`，避免只能手改 SQLite |
| C4 | **Readiness** | `/health`（liveness）与 `/ready`（依赖就绪）分离；前端可选先探活再 `initProjects` |
| C5 | **本机急救（文档即可）** | 开发者若已卡住：将 `task_outbox` 中长期 PENDING 标 `DEAD`，或临时 `FORMUMIND_CELERY_EAGER=false` **且**确保 Redis/worker，或应用 A1 补丁 |

**预估**: 0.5–1 人日（可与 A/B 并行文档部分）

---

## 4. 非目标 / 明确不做（本轮）

- 不把 PubChem enrich、ColBERT bootstrap 当作本症状主修点（已有后台化；且 skip bootstrap 仍复现）  
- 不改中栏研究流水线业务逻辑  
- 不在本计划中「清空用户生产 outbox」——仅本地复现库做过 DEAD 标记作对照  

---

## 5. 建议实施顺序与 PR 切分

```
PR-L1 (P0): Wave A1–A5   ← 根治「一启动中栏就挂」
PR-L2 (P1): Wave B1–B4   ← 即使用户环境仍短暂不可达，文案不再误导
PR-L3 (P2): Wave C       ← DX / 运维，可随后
```

依赖：L2 不依赖 L1 即可合并（改善体验）；但 **只有 L1 能从根上消灭「冷启动必红」**。

---

## 6. 临时缓解（实施前）

1. 确认后端进程已出现 `Application startup complete` / `Uvicorn running` 再开前端  
2. 检查 `SELECT status, operation, count(*) FROM task_outbox GROUP BY 1,2;`  
3. 开发库可将陈旧 `PENDING`/`CLAIMED` 标为 `DEAD` 后重启（**勿对未备份的生产库盲目执行**）  
4. 本地若必须用 eager：在 L1 合并前尽量保持 outbox 干净  

---

## 7. 验收清单（全部完成后）

- [ ] 积压 outbox + eager 冷启动：≤5s 可访问 `/api/projects`  
- [ ] 无 Redis + non-eager 冷启动：≤5s `/health` 200  
- [ ] 停后端刷新：中栏中文「后端不可达」+ uvicorn 命令（含原 `Load failed` / `-> 500` 场景）  
- [ ] 后端恢复后：重试或自动清 error，项目列表正常  
- [ ] 相关 pytest / vitest 绿灯  

---

## 8. 附录：关键日志指纹

**阻塞中（eager + 积压）**

```
INFO: Waiting for application startup.
... Falling back to offline recommend ...
... HTTP Request: GET https://pubchem.ncbi.nlm.nih.gov/...
# 长时间无 Application startup complete
```

**阻塞中（non-eager + 无 Redis）**

```
INFO: Waiting for application startup.
ERROR celery.backends.redis: Connection to Redis lost: Retry (n/20)
```

**健康**

```
INFO: Application startup complete.
INFO: Uvicorn running on http://127.0.0.1:8000
```
