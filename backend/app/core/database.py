import asyncpg

from app.core.config import Settings
from app.core.logging import get_logger

_pool: asyncpg.Pool | None = None
_configured = False
_last_connection_error: str | None = None
logger = get_logger(__name__)


async def initialize_db_pool(settings: Settings) -> asyncpg.Pool:
    global _pool, _configured, _last_connection_error

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to initialize the database pool.")

    _configured = True

    if _pool is None:
        try:
            _pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=settings.database_pool_min_size,
                max_size=settings.database_pool_max_size,
                command_timeout=settings.database_command_timeout,
            )
            _last_connection_error = None
            logger.info(
                "database.pool.initialized",
                min_size=settings.database_pool_min_size,
                max_size=settings.database_pool_max_size,
            )
        except Exception as exc:
            _last_connection_error = str(exc)
            logger.exception("database.pool.initialization_failed")
            raise

    return _pool


async def close_db_pool() -> None:
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("database.pool.closed")


def get_db_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized.")

    return _pool


def is_db_configured() -> bool:
    return _configured


def get_last_connection_error() -> str | None:
    return _last_connection_error


async def check_db_connection() -> bool:
    if _pool is None:
        return False

    try:
        async with _pool.acquire() as connection:
            await connection.fetchval("SELECT 1;")
    except Exception:
        logger.exception("database.healthcheck.failed")
        return False

    return True
