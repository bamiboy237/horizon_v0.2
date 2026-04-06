"""Unit tests for structured logging configuration."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import structlog
import pytest

from app.core.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def restore_logging_state() -> Iterator[None]:
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_context = structlog.contextvars.get_contextvars().copy()

    yield

    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root_logger.setLevel(original_level)
    if original_context:
        structlog.contextvars.bind_contextvars(**original_context)


def test_configure_logging_binds_context_and_sets_level() -> None:
    logging.getLogger().setLevel(logging.INFO)
    configure_logging("warning", "test")

    context = structlog.contextvars.get_contextvars()

    assert context["app_env"] == "test"


def test_get_logger_returns_structlog_logger() -> None:
    configure_logging()

    logger = get_logger("app.core.logging")

    assert hasattr(logger, "info")