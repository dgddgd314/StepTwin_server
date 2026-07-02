from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from steptwin_api.api.routes.database_guard import require_postgresql_database
from steptwin_api.core.config import Settings, get_settings
from steptwin_api.db.session import session_context
from steptwin_api.schemas.pedestrian_graph import (
    PedestrianGraphDataset,
    PedestrianGraphImportRequest,
    PedestrianGraphImportResponse,
    PedestrianGraphValidationResponse,
)
from steptwin_api.services.pedestrian_graph import (
    import_pedestrian_graph_dataset,
    validate_pedestrian_graph_dataset,
)

router = APIRouter(prefix="/pedestrian-graphs", tags=["pedestrian-graphs"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/validate",
    response_model=PedestrianGraphValidationResponse,
    summary="Validate pedestrian graph data before pgRouting import",
)
async def validate_pedestrian_graph(
    dataset: PedestrianGraphDataset,
) -> PedestrianGraphValidationResponse:
    return validate_pedestrian_graph_dataset(dataset)


@router.post(
    "/import",
    response_model=PedestrianGraphImportResponse,
    summary="Import pedestrian graph data into PostGIS tables for pgRouting",
)
async def import_pedestrian_graph(
    request: PedestrianGraphImportRequest,
    settings: SettingsDep,
) -> PedestrianGraphImportResponse:
    require_postgresql_database(settings)

    try:
        async with session_context() as session:
            return await import_pedestrian_graph_dataset(
                session,
                request.dataset,
                replace_existing=request.replace_existing,
            )
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"pgRouting database is not available: {exc.__class__.__name__}",
        ) from exc
