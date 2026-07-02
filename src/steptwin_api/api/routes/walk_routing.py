from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from steptwin_api.api.routes.database_guard import require_postgresql_database
from steptwin_api.core.config import Settings, get_settings
from steptwin_api.db.session import session_context
from steptwin_api.schemas.walk_routing import (
    WalkRouteMetrics,
    WalkRouteOptimizeRequest,
    WalkRouteOptimizeResponse,
    WalkRouteSnappedEndpoint,
    WalkRouteStep,
)
from steptwin_api.services.pgrouting_micro_routing import (
    PgRoutingError,
    PgRoutingGraphConfig,
    PgRoutingNoPathError,
    PgRoutingPedestrianRoute,
    PgRoutingSnapError,
    find_pgrouting_walk_route,
)

router = APIRouter(prefix="/walk-routes", tags=["walk-routes"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/optimize",
    response_model=WalkRouteOptimizeResponse,
    summary="Optimize one pedestrian route with pgRouting",
)
async def optimize_walk_route(
    request: WalkRouteOptimizeRequest,
    settings: SettingsDep,
) -> WalkRouteOptimizeResponse:
    require_postgresql_database(settings)

    try:
        async with session_context() as session:
            route = await find_pgrouting_walk_route(
                session,
                request.start.coordinate,
                request.end.coordinate,
                request.preferences,
                graph_config=build_walk_route_graph_config(settings),
            )
    except PgRoutingSnapError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PgRoutingNoPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"pgRouting database is not available: {exc.__class__.__name__}",
        ) from exc
    except PgRoutingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return build_walk_route_response(route)


def build_walk_route_graph_config(settings: Settings) -> PgRoutingGraphConfig:
    return PgRoutingGraphConfig(
        edge_table=settings.pedestrian_graph_edge_table,
        vertex_table=settings.pedestrian_graph_vertex_table,
    )


def build_walk_route_response(route: PgRoutingPedestrianRoute) -> WalkRouteOptimizeResponse:
    return WalkRouteOptimizeResponse(
        route_kind=route.route_kind,
        start=WalkRouteSnappedEndpoint(
            vertex_id=route.start.vertex_id,
            coordinate=route.start.coordinate,
            snap_distance_meters=route.start.snap_distance_meters,
        ),
        end=WalkRouteSnappedEndpoint(
            vertex_id=route.end.vertex_id,
            coordinate=route.end.coordinate,
            snap_distance_meters=route.end.snap_distance_meters,
        ),
        geometry=list(route.geometry),
        metrics=WalkRouteMetrics(
            total_cost_seconds=route.total_cost_seconds,
            total_distance_meters=route.total_distance_meters,
            duration_seconds=route.duration_seconds,
            stairs_count=route.stairs_count,
            shade_shelters=route.shade_shelters,
        ),
        steps=[
            WalkRouteStep(
                path_seq=step.path_seq,
                node_id=step.node_id,
                edge_id=step.edge_id,
                cost_seconds=step.cost_seconds,
                agg_cost_seconds=step.agg_cost_seconds,
                distance_meters=step.distance_meters,
                stairs_count=step.stairs_count,
                shade_score=step.shade_score,
                corner_count=step.corner_count,
                slope_grade=step.slope_grade,
                geometry=list(step.geometry),
            )
            for step in route.steps
        ],
    )
