"""B-8: literature provider 结构化健康事件 + 熔断器。

根因：provider 失败被 ``degrade_return`` 吞成空列表，调用方看不到
*哪个* provider 挂了、*为什么*挂、*是否该退避*；唯一的重试只覆盖
OpenAlex 429（固定延迟），且没有熔断——挂掉的 provider 每次检索都被
 hammer，拖慢整条文献链。

本模块提供：
- ``classify_error``：异常 → 结构化错误种类；
- ``record_provider_success`` / ``record_provider_failure``：记录事件、
  更新熔断器状态；
- ``provider_breaker_open``：熔断中则跳过调用（fail-open，返回空结果）；
- ``recent_provider_events`` / ``provider_health_snapshot``：供排障与
  B-9 仪表盘查询；
- ``backoff_delay``：指数退避 + 抖动。

事件详情永不携带 URL 查询串（可能含 api_key），只保留异常类名与
脱敏后的短消息。
"""
from __future__ import annotations

import logging
import random
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)

#: 支持熔断的 provider 名（与 search_providers 中的接线一致）。
PROVIDERS = (
    "openalex",
    "serpapi_scholar",
    "serpapi_patents",
    "tavily",
    "serpapi_web",
    "cnipa",
)

_ERROR_KINDS = (
    "timeout",
    "rate_limited",
    "http_5xx",
    "connection",
    "auth",
    "other",
)

_EVENTS: deque["ProviderEvent"] = deque(maxlen=200)
#: provider -> {"consecutive_failures": int, "open_until": float}
_BREAKERS: dict[str, dict[str, float]] = {}

_API_KEY_RE = re.compile(r"(api_key|apikey|key|token)=[^&\s]+", re.IGNORECASE)


@dataclass
class ProviderEvent:
    provider: str
    outcome: str  # "ok" | "error" | "breaker_open_skip"
    latency_ms: float | None = None
    error_kind: str | None = None
    detail: str = ""
    at: float = field(default_factory=time.time)


def _sanitize_detail(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    # 去掉可能含密钥的查询串，只保留短消息。
    text = _API_KEY_RE.sub(r"\1=<redacted>", text)
    text = re.sub(r"\?.{0,200}", "", text)
    return text[:200]


def classify_error(exc: BaseException) -> str:
    """异常 → 结构化错误种类。"""
    name = type(exc).__name__
    text = f"{name} {exc}".lower()

    if "timeout" in name.lower() or "timed out" in text or "timeout" in text:
        return "timeout"
    if "429" in text or "rate limit" in text or "rate_limited" in text:
        return "rate_limited"
    if "connect" in name.lower() or "connection" in text or "network" in text:
        return "connection"
    # httpx.HTTPStatusError：按状态码细分。
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        m = re.search(r"\bstatus[_\s]?code[\"']?\s*[:=]\s*(\d{3})", text)
        if m:
            status = int(m.group(1))
    if isinstance(status, int):
        if status == 429:
            return "rate_limited"
        if status in (401, 403):
            return "auth"
        if 500 <= status <= 599:
            return "http_5xx"
    if "unauthorized" in text or "forbidden" in text or "401" in text or "403" in text:
        return "auth"
    return "other"


def _breaker_state(provider: str) -> dict[str, float]:
    st = _BREAKERS.get(provider)
    if st is None:
        st = {"consecutive_failures": 0.0, "open_until": 0.0}
        _BREAKERS[provider] = st
    return st


def provider_breaker_open(provider: str, *, threshold: int, cooldown_sec: float) -> bool:
    """熔断器是否打开（打开则调用方应跳过本次请求）。"""
    st = _breaker_state(provider)
    if st["open_until"] > time.time():
        return True
    # 冷却期已过但失败计数仍在：半开，下一次调用即探测。
    return False


def record_provider_success(provider: str, started_monotonic: float) -> None:
    latency_ms = (time.monotonic() - started_monotonic) * 1000.0
    _EVENTS.append(
        ProviderEvent(provider=provider, outcome="ok", latency_ms=round(latency_ms, 1))
    )
    _breaker_state(provider)["consecutive_failures"] = 0.0


def record_provider_failure(
    provider: str,
    exc: BaseException,
    *,
    threshold: int = 5,
    cooldown_sec: float = 300.0,
) -> str:
    """记录失败并更新熔断器；返回错误种类。熔断打开时记结构化告警。"""
    kind = classify_error(exc)
    _EVENTS.append(
        ProviderEvent(
            provider=provider,
            outcome="error",
            error_kind=kind,
            detail=_sanitize_detail(exc),
        )
    )
    st = _breaker_state(provider)
    st["consecutive_failures"] += 1
    n = int(st["consecutive_failures"])
    logger.warning(
        "provider_health provider=%s outcome=error kind=%s failures=%d detail=%s",
        provider,
        kind,
        n,
        _sanitize_detail(exc),
    )
    if n >= threshold and st["open_until"] <= time.time():
        st["open_until"] = time.time() + cooldown_sec
        logger.warning(
            "provider_health provider=%s BREAKER OPEN (%d consecutive failures, "
            "cooldown %.0fs)",
            provider,
            n,
            cooldown_sec,
        )
    return kind


def record_breaker_skip(provider: str) -> None:
    _EVENTS.append(ProviderEvent(provider=provider, outcome="breaker_open_skip"))


def recent_provider_events(limit: int = 50) -> list[dict]:
    """最近的 provider 健康事件（新 → 旧），供排障 / B-9 仪表盘。"""
    return [asdict(e) for e in list(_EVENTS)[-limit:][::-1]]


def provider_health_snapshot() -> dict[str, dict]:
    """每个 provider 的熔断器快照。"""
    now = time.time()
    return {
        p: {
            "consecutive_failures": int(_breaker_state(p)["consecutive_failures"]),
            "breaker_open": _breaker_state(p)["open_until"] > now,
        }
        for p in PROVIDERS
    }


def backoff_delay(attempt: int, *, base: float = 1.0, cap: float = 30.0) -> float:
    """指数退避 + 抖动：attempt 从 0 起。"""
    return min(base * (2**attempt), cap) + random.uniform(0, 0.5)


def reset_provider_health() -> None:
    """测试接缝：清空事件与熔断器状态。"""
    _EVENTS.clear()
    _BREAKERS.clear()
