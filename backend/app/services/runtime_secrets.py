"""Thread-safe runtime overlay for secrets and mutable LLM settings.

``Settings`` (``@lru_cache``) stays immutable after load; UI / API updates write here.
"""
from __future__ import annotations

import threading
from typing import Any

from ..config import Settings

_LLM_RUNTIME_ATTRS = frozenset({"llm_provider", "llm_model", "llm_base_url"})


class RuntimeSecrets:
    """Process-wide, thread-safe store for secrets and runtime LLM config."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._overrides: dict[str, str | None] = {}

    def clear(self) -> None:
        with self._lock:
            self._overrides.clear()

    def load_from_settings(
        self,
        settings: Settings,
        secret_attrs: tuple[str, ...] | list[str],
    ) -> None:
        with self._lock:
            self._overrides.clear()
            for attr in secret_attrs:
                if hasattr(settings, attr):
                    self._overrides[attr] = getattr(settings, attr, None)

    def get(self, attr: str) -> str | None | _Unset:
        with self._lock:
            if attr in self._overrides:
                return self._overrides[attr]
            return _UNSET

    def set(self, attr: str, value: str | None) -> None:
        with self._lock:
            self._overrides[attr] = value

    def effective(self, settings: Settings, attr: str) -> Any:
        with self._lock:
            if attr in self._overrides:
                return self._overrides[attr]
            return getattr(settings, attr, None)

    def snapshot(self) -> dict[str, str | None]:
        with self._lock:
            return dict(self._overrides)


class _Unset:
    pass


_UNSET = _Unset()

_store = RuntimeSecrets()


def get_runtime_secrets() -> RuntimeSecrets:
    return _store


def reset_runtime_secrets() -> None:
    """Test helper — clear the process-wide store."""
    _store.clear()


def effective_setting(settings: Settings, attr: str) -> Any:
    return get_runtime_secrets().effective(settings, attr)


def secret_attrs_from_registry(registry: list[tuple[str, str, str, str]]) -> tuple[str, ...]:
    return tuple(attr for attr, _, _, _ in registry)
