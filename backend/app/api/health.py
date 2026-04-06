"""Health check endpoints for service readiness and liveness probes."""

from fastapi import APIRouter

from app.core.database import check_db_connection, get_last_connection_error, is_db_configured

router = APIRouter()


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    if not is_db_configured():
        return {"status": "ok", "database": "not_configured"}

    database_status = "connected" if await check_db_connection() else "unavailable"
    status = "ok" if database_status == "connected" else "degraded"
    response = {"status": status, "database": database_status}
    if database_status == "unavailable":
        response["database_error"] = get_last_connection_error() or "connection failed"
    return response
