from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from steptwin_api.core.config import Settings, get_settings
from steptwin_api.db.session import ping_database
from steptwin_api.schemas.health import ComponentHealth, HealthResponse, OverallStatus

router = APIRouter(tags=["health"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
)
async def read_health(
    response: Response,
    settings: SettingsDep,
) -> HealthResponse:
    database = await get_database_health()
    overall_status: OverallStatus = "degraded" if database.status == "degraded" else "ok"

    if overall_status == "degraded":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall_status,
        service=settings.app_name,
        environment=settings.app_env,
        version=settings.app_version,
        timestamp=datetime.now(UTC),
        checks={
            "application": ComponentHealth(status="ok"),
            "database": database,
        },
    )


async def get_database_health() -> ComponentHealth:
    try:
        is_ready = await ping_database()
    except Exception as exc:
        return ComponentHealth(status="degraded", detail=exc.__class__.__name__)

    if is_ready is None:
        return ComponentHealth(status="disabled", detail="DATABASE_URL is not configured")

    return ComponentHealth(status="ok")
