"""Tests for tiered exception handling utilities."""
from __future__ import annotations

import logging

import httpx
import pytest

from app.services.errors import (
    FatalError,
    PermanentError,
    TransientError,
    classify_exception,
    degrade_return,
    log_handled_exception,
    optional_import,
    reraise_if_fatal,
)


def test_reraise_if_fatal_propagates_memory_error():
    with pytest.raises(MemoryError):
        reraise_if_fatal(MemoryError("oom"))


def test_classify_transient_timeout():
    assert classify_exception(TimeoutError()) == "transient"
    assert classify_exception(TransientError("x")) == "transient"
    assert classify_exception(httpx.TimeoutException("x")) == "transient"


def test_classify_permanent_value_error():
    assert classify_exception(ValueError("bad")) == "permanent"
    assert classify_exception(PermanentError("x")) == "permanent"


def test_degrade_return_preserves_default():
    logger = logging.getLogger("test.errors")
    assert degrade_return(logger, TimeoutError("t"), "ctx", []) == []


def test_degrade_return_reraises_fatal():
    with pytest.raises(MemoryError):
        degrade_return(logging.getLogger("test.errors"), MemoryError(), "ctx", [])


def test_optional_import_missing_module():
    assert optional_import("formumind_nonexistent_module_xyz") is False


def test_log_handled_exception_levels(caplog):
    logger = logging.getLogger("test.errors.levels")
    with caplog.at_level(logging.INFO):
        kind = log_handled_exception(logger, ValueError("bad input"), "unit test")
    assert kind == "permanent"
