import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO", app_env: str = "development") -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(app_env=app_env)


def get_logger(name: str):
    return structlog.get_logger(name)
