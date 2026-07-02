from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from steptwin_api.core.config import Settings, get_settings
from steptwin_api.db.session import session_context
from steptwin_api.schemas.routing import (
    Place,
    RenderStyle,
    RouteDebug,
    RouteMarker,
    RoutePreviewRequest,
    RoutePreviewResponse,
    RouteSegment,
    RouteSummary,
    RoutingPreferences,
    SegmentMetrics,
    Viewport,
)
from steptwin_api.services.geometry import viewport_for
from steptwin_api.services.macro_routing import DemoMacroRouter, TmapMacroRouter, TransitSkeleton
from steptwin_api.services.micro_routing import DemoMicroRouter, WalkingRoute
from steptwin_api.services.pgrouting_micro_routing import (
    PgRoutingError,
    PgRoutingGraphConfig,
    PgRoutingPedestrianRoute,
    find_pgrouting_walk_route,
)


class RoutePreviewService:
    def __init__(
        self,
        macro_router: DemoMacroRouter | TmapMacroRouter | None = None,
        micro_router: DemoMicroRouter | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._macro_router = macro_router or build_macro_router(self._settings)
        self._micro_router = micro_router or DemoMicroRouter()

    async def build_preview(self, request: RoutePreviewRequest) -> RoutePreviewResponse:
        skeleton = self._macro_router.build_transit_skeleton(request.origin, request.destination)

        first_mile, first_source = await self._build_walk(
            segment_id="walk-first-mile",
            start=request.origin,
            end=skeleton.boarding_stop,
            title="Stair-minimized first mile",
            preferences=request.preferences,
        )
        last_mile, last_source = await self._build_walk(
            segment_id="walk-last-mile",
            start=skeleton.alighting_stop,
            end=request.destination,
            title="Shade-first last mile",
            preferences=request.preferences,
        )
        transit_segment = build_transit_segment(skeleton)

        segments = [first_mile.segment, transit_segment, last_mile.segment]
        markers = [
            RouteMarker(
                id="origin",
                kind="origin",
                title=request.origin.name,
                coordinate=request.origin.coordinate,
                icon="origin",
            ),
            RouteMarker(
                id="boarding-stop",
                kind="stop",
                title=skeleton.boarding_stop.name,
                coordinate=skeleton.boarding_stop.coordinate,
                segment_id=transit_segment.id,
                icon="transit-stop",
            ),
            RouteMarker(
                id="alighting-stop",
                kind="stop",
                title=skeleton.alighting_stop.name,
                coordinate=skeleton.alighting_stop.coordinate,
                segment_id=transit_segment.id,
                icon="transit-stop",
            ),
            RouteMarker(
                id="destination",
                kind="destination",
                title=request.destination.name,
                coordinate=request.destination.coordinate,
                icon="destination",
            ),
            *first_mile.markers,
            *last_mile.markers,
        ]
        all_points = [point for segment in segments for point in segment.geometry]
        southwest, northeast = viewport_for(all_points)

        return RoutePreviewResponse(
            route_id=uuid4(),
            summary=build_summary(segments),
            segments=segments,
            markers=markers,
            viewport=Viewport(southwest=southwest, northeast=northeast),
            debug=RouteDebug(
                macro_router=get_macro_router_name(self._settings),
                micro_router=build_micro_router_debug_name(first_source, last_source),
                tmap_live_sync=self._settings.tmap_use_live
                and self._settings.tmap_app_key is not None,
                note=build_debug_note(first_source, last_source),
            ),
        )

    async def _build_walk(
        self,
        *,
        segment_id: str,
        start: Place,
        end: Place,
        title: str,
        preferences: RoutingPreferences,
    ) -> tuple[WalkingRoute, str]:
        if can_use_postgresql_database(self._settings):
            try:
                async with session_context() as session:
                    route = await find_pgrouting_walk_route(
                        session,
                        start.coordinate,
                        end.coordinate,
                        preferences,
                        graph_config=build_pgrouting_graph_config(self._settings),
                    )
                if route_snap_distance_is_acceptable(route, self._settings):
                    return build_walking_route_from_pgrouting(segment_id, title, route), "pgrouting"
            except (RuntimeError, SQLAlchemyError, PgRoutingError):
                pass

        return (
            self._micro_router.build_custom_walk(
                segment_id=segment_id,
                start=start,
                end=end,
                title=title,
                preferences=preferences,
            ),
            "demo",
        )


def build_macro_router(settings: Settings) -> DemoMacroRouter | TmapMacroRouter:
    if settings.tmap_use_live:
        return TmapMacroRouter(settings=settings)

    return DemoMacroRouter()


def get_macro_router_name(settings: Settings) -> str:
    if settings.tmap_use_live:
        return "live-tmap-adapter"

    return "demo-tmap-adapter"


def can_use_postgresql_database(settings: Settings) -> bool:
    if settings.database_url is None:
        return False

    return make_url(settings.database_url).drivername.startswith("postgresql")


def build_pgrouting_graph_config(settings: Settings) -> PgRoutingGraphConfig:
    return PgRoutingGraphConfig(
        edge_table=settings.pedestrian_graph_edge_table,
        vertex_table=settings.pedestrian_graph_vertex_table,
    )


def route_snap_distance_is_acceptable(
    route: PgRoutingPedestrianRoute,
    settings: Settings,
) -> bool:
    max_distance = settings.pedestrian_graph_max_snap_distance_meters
    return (
        route.start.snap_distance_meters <= max_distance
        and route.end.snap_distance_meters <= max_distance
    )


def build_walking_route_from_pgrouting(
    segment_id: str,
    title: str,
    route: PgRoutingPedestrianRoute,
) -> WalkingRoute:
    stairs_avoided = 0 if route.route_kind == "weighted" else route.stairs_count
    return WalkingRoute(
        segment=RouteSegment(
            id=segment_id,
            kind="custom_walk",
            mode="walk",
            title=title,
            geometry=list(route.geometry),
            render=RenderStyle(color="#16A34A", width=6, pattern="dashed"),
            metrics=SegmentMetrics(
                distance_meters=route.total_distance_meters,
                duration_seconds=route.duration_seconds,
                shade_shelters=route.shade_shelters,
                stairs_avoided=stairs_avoided,
            ),
        ),
        markers=build_pgrouting_walk_markers(segment_id, route),
    )


def build_pgrouting_walk_markers(
    segment_id: str,
    route: PgRoutingPedestrianRoute,
) -> list[RouteMarker]:
    markers: list[RouteMarker] = []
    for index, step in enumerate(
        [step for step in route.steps if step.shade_score >= 0.45][:2],
        start=1,
    ):
        markers.append(
            RouteMarker(
                id=f"{segment_id}-shade-{index}",
                kind="shade_shelter",
                title="Shade shelter" if index == 1 else "Tree shade",
                coordinate=step.geometry[-1],
                segment_id=segment_id,
                icon="parasol" if index == 1 else "tree",
            )
        )

    return markers


def build_micro_router_debug_name(first_source: str, last_source: str) -> str:
    if first_source == last_source == "pgrouting":
        return "postgis-pgrouting-pedestrian-router"
    if first_source == last_source == "demo":
        return "demo-weighted-pedestrian-router"

    return "mixed-pgrouting-demo-pedestrian-router"


def build_debug_note(first_source: str, last_source: str) -> str:
    if first_source == last_source == "pgrouting":
        return (
            "Route preview uses the PostGIS pedestrian graph "
            "pedestrian_vertices/pedestrian_edges for walking segments."
        )

    return (
        "Route preview uses pgRouting when endpoints snap to the MVP graph; "
        "out-of-coverage walking segments fall back to the demo router."
    )


def build_transit_segment(skeleton: TransitSkeleton) -> RouteSegment:
    return RouteSegment(
        id="transit-main",
        kind="transit",
        mode=skeleton.transit.mode,
        title=f"Ride {skeleton.transit.route_name}",
        geometry=skeleton.geometry,
        render=RenderStyle(color="#2563EB", width=7, pattern="solid"),
        metrics=SegmentMetrics(
            distance_meters=skeleton.distance_meters,
            duration_seconds=skeleton.duration_seconds,
            shade_shelters=0,
            stairs_avoided=0,
        ),
        transit=skeleton.transit,
    )


def build_summary(segments: list[RouteSegment]) -> RouteSummary:
    total_distance = sum(segment.metrics.distance_meters for segment in segments)
    total_duration = sum(segment.metrics.duration_seconds for segment in segments)
    walking_distance = sum(
        segment.metrics.distance_meters for segment in segments if segment.kind == "custom_walk"
    )
    transit_distance = sum(
        segment.metrics.distance_meters for segment in segments if segment.kind == "transit"
    )

    return RouteSummary(
        total_distance_meters=total_distance,
        total_duration_seconds=total_duration,
        walking_distance_meters=walking_distance,
        transit_distance_meters=transit_distance,
        shade_shelters=sum(segment.metrics.shade_shelters for segment in segments),
        stairs_avoided=sum(segment.metrics.stairs_avoided for segment in segments),
    )
