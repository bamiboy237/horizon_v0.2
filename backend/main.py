from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import close_db_pool, initialize_db_pool
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.app_env)

    logger = get_logger(__name__)
    logger.info("app.startup", app_name=settings.app_name, app_env=settings.app_env)

    if settings.database_url:
        try:
            await initialize_db_pool(settings)
        except Exception:
            logger.warning("database.pool.degraded_startup")
    else:
        logger.warning("database.pool.skipped", reason="DATABASE_URL is not configured")

    try:
        yield
    finally:
        await close_db_pool()
        logger.info("app.shutdown", app_name=settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(title="Horizon v0.2 Backend", lifespan=lifespan)
    app.include_router(api_router)
    return app


app = create_app()
