"""Shared pytest fixtures and test utilities for the backend suite."""

from __future__ import annotations

import sys
from pathlib import Path
from collections.abc import Iterator

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core import database
from app.core.config import get_settings
from app.core import security


@pytest.fixture(autouse=True)
def reset_backend_state() -> Iterator[None]:
    get_settings.cache_clear()
    security._load_clerk_sdk.cache_clear()
    database._pool = None
    database._configured = False
    database._last_connection_error = None
    yield
    get_settings.cache_clear()
    security._load_clerk_sdk.cache_clear()
    database._pool = None
    database._configured = False
    database._last_connection_error = None