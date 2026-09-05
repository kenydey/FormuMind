# FormuMind Bug 全量排查与修复计划

> 日期: 2026-09-05  
> 依据: `main` @ `18cc8d0`、CI run `33960508962`（49 failed / 1748 passed）、代码实读与本地复现  
> 目标: 先止血 CI / 生产可观测性，再修功能正确性，最后清债务

---

## 0. 执行摘要

| 严重级 | 数量 | 典型症状 | 建议窗口 |
|--------|------|----------|----------|
| **P0** | 4 | CI 全红、异步任务 503、日志占位符失效、LHS NameError | 立即（1 个 PR） |
| **P1** | 6 | 推荐 120s 误杀、混料静默降级、KG 门禁死代码、聊天单测回归、RDKit/openai CI 缺口 | 本周 |
| **P2** | 7 | 排行榜索引错位、摩尔质量单位丢弃、可行性 fail-open、Neo4j 白名单等 | 下周 |
| **已知 flaky** | 1 | `test_kb_ingest_queue` 外网超时 | 随手标记 |

当前 CI：`backend` 49 失败；`frontend` 在 `npm ci` 因 `@esbuild/aix-ppc64` 平台包直接失败。

---

## 1. P0 — 立即修复（建议单 PR「ci-hotfix」）

### B1. loguru 日志占位符被误改成 `%s`（回归，commit `18cc8d0`）

**实证**

- CI 日志原文：`celery dispatch timed out for %s: %s`（参数未插值）
- `18cc8d0` 改了 15 个文件；其中 **14 个 `from loguru import logger`**，仅 `colbert_store.py` 是 stdlib `logging`
- loguru 语法是 `"… {} …", arg`；`%s` 会被当成字面量

**根因**  
把 loguru 当成 stdlib `logging` 的 `%-format` 来「修」，导致关键错误日志静默变废。

**修复**

1. 对 loguru 模块：把 `18cc8d0` 中的 `%s/%r` **改回 `{}`/`{!r}`**（或统一改用 f-string，二选一，全仓一致）
2. 仅保留 `colbert_store.py`（stdlib）的 `%s`
3. 补扫：`measurement_store.py:196` 等同款 loguru+`%s` 漏网
4. 加防护测试或脚本：禁止 `from loguru import logger` 的文件里出现 `logger.(debug|info|warning|error|exception)\(["'].*%[sd]`

**验收**: CI/本地触发一次 dispatch 失败，日志中能看到真实 `kind` 与异常文本。

---

### B2. `celery_eager` 默认 `False` 打挂无 Redis 的测试（回归，commit `9928c1b`）

**实证**

- ~15+ 用例期望 `202`，实得 `503`（Redis connection refused / `task.delay > 10s`）
- 波及：`test_recommend_async_sse`、`test_tasks_sse`、`test_integrations`、`test_inverse_design`、`test_sse_disk_fallback`、`test_search_incremental`、`test_requirement_upgrade`、`test_dependencies`、`test_tasks_owner`、`test_phase_abc_e2e`、`test_knowledge_cohort`、`test_api` 等
- `test_sse_disk_fallback.py` 文档写明：**Eager recommend + disk terminal** 才应在无 Redis 时完成
- `conftest.py` **未**设置 `FORMUMIND_CELERY_EAGER=true`
- CI workflow **无** Redis service

**根因**  
生产默认改 `eager=False` 正确；测试环境未同步 opt-in eager，也未起 Redis。

**修复（推荐组合）**

1. **`backend/tests/conftest.py`**：`os.environ.setdefault("FORMUMIND_CELERY_EAGER", "true")`（测试默认同步执行；Settings 前缀为 `FORMUMIND_`）
2. 需要测「真异步 / broker 探活」的用例显式 `monkeypatch` 为 `False`（已有 `test_dispatch_guard` 模式）
3. （可选，中期）CI 增加 `services: redis` + 一组 non-eager 集成 job
4. 修正 `backend/app/worker/celery_app.py` 过时注释（仍写 default True）

**验收**: 上述 503 类用例全绿；`test_dispatch_guard` 仍覆盖 broker down → 503。

---

### B3. CI 缺少科学/LLM 可选依赖 → RDKit / openai 相关大面积失败

**实证**

- `No module named 'rdkit'` → `test_chemistry` / `test_chemtools` / `test_structure_retrieval`（约 24 失败）及部分 chem_gate
- `未安装 openai SDK` → `test_vision_settings_api::test_deepseek_vision_model_is_accepted`
- `requirements.txt` 为核心集；`rdkit` / `openai` 在 extras（`pyproject.toml`）

**修复（二选一，推荐 A）**

- **A. CI 安装测试所需 extras**：`pip install -e '.[dev,llm,science]'`（或最小子集 `rdkit`+`openai`）
- **B. 无依赖时 `pytest.importorskip` / 标记 skip**（会降低 CI 覆盖，仅作临时）

**验收**: chemistry / chemtools / structure_retrieval / vision deepseek 相关用例不再因缺包失败。

---

### B4. DOE cycle LHS 回退 `NameError`（功能阻断）

**实证** — `backend/app/services/doe_cycle_service.py:76-84`

```python
exp_dict = {
    ...
    "infeasible_reason": exp_dict["infeasible_reason"]  # NameError: 构造中自引用
}
```

BayBE 路径（`:106`）正确使用 `run.infeasible_reason`。LHS 回退必崩，外层 `except` 只返回 `"Generation failed"`。

**修复**: 改为 `run.infeasible_reason`；补单测 mock BayBE unavailable → LHS 路径不抛 NameError。

---

## 2. P1 — 本周修复

### B5. 前端推荐绝对超时 120s vs LLM 174–281s

**证据**: `frontend/src/api.ts` 默认 `timeoutMs = 120_000`；`researchSlice` 传入 `120_000`；注释称总时长不限，但墙钟定时器仍在。今日改 async 后长任务更常见。

**修复**: 推荐/优化等长任务 `timeoutMs = 0`（禁用绝对超时），保留空闲超时；或提高到 ≥600s 并在 UI 显示进度。

---

### B6. 混料 DOE：异常时语义丢失 / native 无 `simplex_lattice`

**证据**: CI `test_doe_engines::test_mixture_design_failure_raises_not_fallback`  
实际：`Unknown design 'simplex_lattice'; choose from [ccd, fractional_factorial, …]`  
`docs/plans/2026-09-04-ai-review-top5-fixes.md` P0 已描述；静默→LHS 风险。

**修复**: 按该文档实施 — 混料集合显式失败或走真正混料 native；禁止塌缩到无约束 LHS；notes 写明原因。

---

### B7. pyDOE KG 相容性门禁死代码

**证据**: `pydoe_engine.py` 错误 import `..services.kg_chemical_check`（多一层）；`except Exception: pass`；且 `requirement` 从未传入。

**修复**: 与 `baybe_engine` 对齐 import；去掉裸 `pass`（至少 warning）；打通 `requirement` 参数。

---

### B8. Chat SSE 单测与「无 API Key」早退不一致

**证据**: CI  
- 期望 `'error'=='phase'` / `'生成失败' in …`  
- 实得 `'未配置 LLM API Key'`  
`chat.py` 在 monkeypatch 流式 LLM **之前** 就因空 key 返回。

**修复**: 测试里注入假 API key；或测试「无 key」专用用例；流式成功路径必须 setenv/monkeypatch `get_active_api_key`。

---

### B9. 前端 `npm ci` 失败（`@esbuild/aix-ppc64` EBADPLATFORM）

**证据**: CI frontend job 在 Install dependencies 失败；`package-lock.json` 含 aix-ppc64 optional 条目，npm 在 linux 上仍尝试安装。

**修复**: 用当前 CI Node 版本重生成 `package-lock.json`；或 `npm ci --omit=optional`（需确认无必要 optional）；锁定 npm 主版本与本地一致。

---

### B10. SQLite KG 关系层为空（产品功能缺口）

**证据**: 决策记录 — 实体 715 / 提及 7 万 / `kb_entity_links=0`；`kg_relations_on_ingest=False`。

**修复**: 默认或管理入口启用关系抽取；运维跑 `POST /api/kg/relations/rebuild`；监控 `kb_entity_links` 计数。

---

## 3. P2 — 下周清理

| ID | 问题 | 位置 | 修复要点 |
|----|------|------|----------|
| B11 | 排行榜删除后 `editIndex`/`aiTargetIdx` 未左移 | `FormulaLeaderboard.tsx` | 删除时重映射 index |
| B12 | DOE「基准」仅按 name 匹配 | 同上 | 用稳定 id |
| B13 | Ingredient `molar_mass` 带单位字符串被丢弃 | `schemas.py` | 解析 `381.9 g/mol` 或与 gate 共用 parser |
| B14 | 可行性审查异常 fail-open | `feasibility.py` | 改为 fail-closed 或 `status=unknown` 降权 |
| B15 | 预测失败写入 `0.0` 伪实测 | `measurements_adapter.py` | 写 None/跳过，禁止当实测 |
| B16 | Neo4j 不在 env-flags 白名单 | `env_flags.py` | 登记 `NEO4J_*`，避免再「死键」 |
| B17 | `saveFormulaToDoe` 吞掉 lineage 错误 | `researchSlice.ts` | 区分空 lineage vs 请求失败 |

---

## 4. 历史文档项复核

| 文档项 | 现状 |
|--------|------|
| paper-qa `aadd_texts` 未 await | 代码侧已 await — **关闭** |
| `NH3·H2O` 水合物 | 需回归单测确认；若仍失败则重开 |
| 项目 workspace 422 | 需确认前端清洗是否已合入；保留监控 |
| `test_kb_ingest_queue` flaky | 标记 `@pytest.mark.flaky` 或 mock 外网 |

---

## 5. 实施顺序与 PR 切分

```
PR-1  ci-hotfix (P0: B1+B2+B3+B4 + B9 lockfile)
        ├─ revert/fix loguru placeholders
        ├─ conftest CELERY_EAGER=true
        ├─ CI extras: rdkit + openai (or science+llm)
        ├─ doe_cycle LHS NameError
        └─ regenerate frontend package-lock
        验收: CI backend+frontend 绿

PR-2  async-ux + doe-correctness (P1: B5+B6+B7+B8)
        验收: 长推荐不误杀；混料单测；chat stream 单测；KG gate 单测

PR-3  product-hardening (P1 B10 + P2 B11–B17)
        验收: 关系重建可观测；排行榜/schema/feasibility 单测
```

---

## 6. 测试策略

| 层级 | 内容 |
|------|------|
| 自动化 | `pytest -m "not golden_eval" -q`；前端 `tsc` + `vitest` + `build` |
| 定向 | dispatch 超时日志插值；eager 下 recommend 202；无 BayBE 的 doe_cycle；混料 raise；chat stream 假 key |
| 手工 | Docker：`CELERY_EAGER=false` + redis + worker → 推荐 202→SSE completed（>120s 不前端超时） |
| 回归门禁 | 合并前 CI 必须绿；禁止再引入 loguru+`%s` |

---

## 7. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 测试默认 eager 掩盖真异步 bug | 保留/扩充 `test_dispatch_guard`；可选 CI redis job |
| CI 装 rdkit 变慢/失败 | 用 conda-forge wheel 或 pin 已知版本；失败则 skip 并告警 |
| 混料改为显式报错影响旧前端 | 前端已有通用错误条；发布说明注明 |
| loguru 改回 `{}` 后 stdlib 文件误改 | 按 import 类型分支处理；加 lint 脚本 |

回滚：PR-1 各文件可独立 revert；`celery_eager` 生产默认保持 `False` 不动。

---

## 8. 成功标准

1. `main` CI：backend + frontend **全绿**
2. 无 Redis 单测环境下异步端点测试不再 503
3. 关键路径 loguru 日志可读（无字面 `%s`）
4. BayBE 不可用时 DOE cycle LHS 回退可用
5. 长耗时推荐不被前端 120s 误杀（PR-2）

---

## 附录 A — CI 失败聚类（run 33960508962）

| 簇 | 约 # | 主因 |
|----|------|------|
| structure_retrieval + chemistry + chemtools + chem_gate | ~24 | 缺 rdkit |
| recommend/tasks/integrations/inverse/sse/search/… 503 | ~15 | eager=false + 无 Redis |
| chat_stream | 3 | 无 API key 早退 vs 单测假设 |
| vision deepseek | 1 | 缺 openai |
| doe_engines mixture | 1 | simplex_lattice / 混料路径 |
| frontend npm ci | 1 job | aix-ppc64 EBADPLATFORM |

## 附录 B — 今日提交与回归热点

| Commit | 影响 |
|--------|------|
| `18cc8d0` fix(log) | **B1** loguru 误改 |
| `9928c1b` fix(celery) | **B2** 测试未跟随；放大 **B5** |
| `24447eb` / `61128a6` 配方卡片 | **B11/B12/B17** |
| `c840323` kg relations | **B10** 需运维重建 |
| `e0fc3aa` molar_mass | **B13** 残余单位字符串 |
