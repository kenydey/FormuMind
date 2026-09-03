# P1 异常透明度硬化（3 处降级点的可控性增强）

> 基于异常审计结论：370 处 `except→降级` 绝大多数为合理防御性容错，**无严重真吞错**。
> 本文针对 3 处"失败可能被上游误判为成功 / 整批失败被静默吞掉"的边界，做**最小可控增强**，
> 不改变成功路径行为，仅提升失败可见性。属低风险优化，非功能变更。

## A1 — claim_checker LLM 失败需与"无 key offline"区分

**位置**：`app/pipeline/claim_checker.py:203-211`

**现状**：有 API key 时尝试 LLM 质检；失败 `except` 后回退 `verify_claim_offline`，但 `engine` 变量保持
初始值 `"offline"`。结果：LLM 实际失败回退 与 无 key 的纯 offline 在 `engine` 字段上**无法区分**，
前端/调用方无法提示"质检降级"。

**改法**：LLM 失败回退时显式标记降级：
```python
except Exception as exc:
    logger.warning("Claim check LLM failed: {}", exc)
    verified = [verify_claim_offline(c, evidence) for c in claims]
    engine = "degraded"   # 明确区分：曾尝试 LLM 但失败
```
`ClaimCheckResult.engine` 当前类型为 `str`（"llm"|"offline"），`"degraded"` 不破坏类型（前端按字符串展示即可）。

**验证**：单测——mock `verify_claims_llm` 抛异常，断言返回 `engine == "degraded"` 且 `verified_claims` 由 offline 填充。
**风险**：低（仅新增一个字符串值，向后兼容）。

## A2 — qc_ingest 实测同步失败不应静默返回 {}

**位置**：`app/services/qc_ingest.py:173-175`

**现状**：`sync_measurement` 写库失败时 `return {}`（与"无测量数据"的成功返回 `{}` 完全相同），
调用方无法区分"真的没数据" vs "同步失败丢数据"。

**改法**：失败时返回带错误标记的 dict（不静默），保留 warning 日志：
```python
except Exception as exc:
    logger.warning("measured sync failed for experiment {}: {}", experiment_id, exc)
    return {"_sync_error": str(exc), "experiment_id": experiment_id}
```
调用方读到 `_sync_error` key 即可判定失败；无此 key 仍视为正常空结果。

**验证**：单测——mock `commit_session` 抛异常，断言返回含 `_sync_error` 且原 `{}` 路径（无 measurement）仍返回 `{}`。
**风险**：低（返回结构多一个可选 key，调用方按 key 存在性判断）。

## A3 — campaign_store.batch_sync 整批失败需整体报错（防静默全丢）

**位置**：`app/db/campaign_store.py:482-537`（循环内 489/534 的 per-item skip）

**现状**：单行 `_get_item`/`_save_item` 失败仅 `logger.warning + continue`，`updated` 计数返回成功数。
若**所有行**都因同一根因（Datalab 不可达）失败，`updated=0` 被当作"成功同步 0 行"返回，整批数据静默丢失。

**改法**：聚合失败数，循环结束后若 `failed == total` 且 `total > 0`，整体 raise（让上层感知，而非静默 0）：
```python
# 循环内：failed += 1（在 491 / 536 的 except 处）
...
if total_rows > 0 and failed == total_rows:
    raise DatalabUnavailableError(self._api_url, f"batch_sync 全部 {total_rows} 行失败（疑似 Datalab 不可达）")
return updated
```
`DatalabUnavailableError` 已在同文件使用（448 行），可直接复用。

**验证**：单测——mock `_get_item` 全部抛异常，断言 `batch_sync` 抛 `DatalabUnavailableError`；单行失败其余成功时不抛。
**风险**：低（仅整批全失败时行为变化：从"返回 0"变"raise"，上层已能处理 DatalabUnavailableError）。

## 文件变更清单

| # | 文件 | 改动 |
|---|------|------|
| A1 | `backend/app/pipeline/claim_checker.py` | LLM 失败回退设 `engine="degraded"` |
| A2 | `backend/app/services/qc_ingest.py` | 失败返回 `_sync_error` 标记 |
| A3 | `backend/app/db/campaign_store.py` | 聚合失败计数，整批全失败时 raise |

## 验证
- 三处各加/改单测，覆盖"降级路径返回可区分结果"
- 核心回归：`tests/test_workbench_api.py` / `tests/test_qc*.py`（若有）/ claim_checker 相关测试全绿
- 前端 tsc 不受影响（仅后端返回结构增加可选 key）

---
*本文全部基于代码现状（`claim_checker.py:203-211` / `qc_ingest.py:160-175` / `campaign_store.py:478-537`）调研，无编造。*
