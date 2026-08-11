"""Tiered exception handling utilities for FormuMind services.

Classifies failures as fatal (must propagate), transient (retry/degrade),
or permanent (skip source).  Use in broad ``except Exception`` handlers
instead of silent ``pass`` or undifferentiated logging.
"""
from __future__ import annotations

import logging
from typing import Literal, TypeVar

T = TypeVar("T")

ErrorKind = Literal["fatal", "transient", "permanent", "unknown"]

_FATAL_TYPES: tuple[type[BaseException], ...] = (
    SystemExit,
    KeyboardInterrupt,
    MemoryError,
    RecursionError,
)

_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
    BrokenPipeError,
    InterruptedError,
)


class TransientError(Exception):
    """Retryable failure — network blip, rate limit, temporary outage."""


class PermanentError(Exception):
    """Non-retryable — bad credentials, invalid query, missing resource."""


class FatalError(Exception):
    """Must not be swallowed — signals operator intervention."""


def _httpx_transient_types() -> tuple[type[BaseException], ...]:
    try:
        import httpx

        return (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.NetworkError,
            httpx.PoolTimeout,
        )
    except ImportError:
        return ()


def reraise_if_fatal(exc: BaseException) -> None:
    """Re-raise exceptions that must never be caught by degrade handlers."""
    if isinstance(exc, _FATAL_TYPES):
        raise exc
    if isinstance(exc, FatalError):
        raise exc


def classify_exception(exc: BaseException) -> ErrorKind:
    """Classify an exception for logging and degrade strategy."""
    if isinstance(exc, _FATAL_TYPES) or isinstance(exc, FatalError):
        return "fatal"
    if isinstance(exc, (TransientError, *_TRANSIENT_TYPES, *_httpx_transient_types())):
        return "transient"
    if isinstance(exc, PermanentError):
        return "permanent"
    # Common permanent HTTP / parse failures
    name = type(exc).__name__
    if name in {
        "HTTPStatusError",
        "JSONDecodeError",
        "UnicodeDecodeError",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "ValidationError",
    }:
        return "permanent"
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
            return "permanent"
        if isinstance(exc, httpx.HTTPStatusError):
            return "transient"
    except ImportError:
        pass
    return "unknown"


def log_handled_exception(
    logger: logging.Logger,
    exc: BaseException,
    context: str,
    *,
    level: int | None = None,
) -> ErrorKind:
    """Log a handled exception with severity matched to its kind. Returns kind."""
    reraise_if_fatal(exc)
    kind = classify_exception(exc)
    # Pre-formatted, single-arg calls: a sizeable share of this codebase's
    # "logger" is actually loguru (str.format()-style, not %-style), and a
    # good chunk of it doesn't shadow that with logging.getLogger the way
    # app/api/tasks.py does. %s args would silently render as the literal
    # text "%s" on those loggers instead of raising, so the exception detail
    # this function exists to capture would just vanish from the log.
    if level is not None:
        logger.log(level, f"{context}: {exc}")
        return kind
    if kind == "transient":
        logger.warning(f"{context} (transient): {exc}")
    elif kind == "permanent":
        logger.info(f"{context} (permanent): {exc}")
    else:
        logger.warning(f"{context}: {exc}")
    return kind


def degrade_return(
    logger: logging.Logger,
    exc: BaseException,
    context: str,
    default: T,
) -> T:
    """Log, classify, and return a degrade default (never swallows fatal errors)."""
    log_handled_exception(logger, exc, context)
    return default


def optional_import(module: str) -> bool:
    """Return True when *module* is importable (narrower than bare except)."""
    try:
        __import__(module)
        return True
    except ImportError:
        return False
    except Exception as exc:
        reraise_if_fatal(exc)
        logging.getLogger(__name__).debug("optional_import(%s): %s", module, exc)
        return False
