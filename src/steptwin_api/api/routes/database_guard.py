from fastapi import HTTPException, status
from sqlalchemy.engine import make_url

from steptwin_api.core.config import Settings


def require_postgresql_database(settings: Settings) -> None:
    if settings.database_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DATABASE_URL is required for pgRouting operations",
        )

    driver_name = make_url(settings.database_url).drivername
    if not driver_name.startswith("postgresql"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DATABASE_URL must use PostgreSQL/PostGIS/pgRouting",
        )
