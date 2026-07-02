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
from steptwin_api.services.macro_routing import (
    DemoMacroRouter,
    TmapMacroRouter,
    TransitLegSkeleton,
    TransitSkeleton,
)
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
        transit_legs = get_transit_legs(skeleton)
        segments = [first_mile.segment]
        markers = [
            RouteMarker(
                id="origin",
                kind="origin",
                title=request.origin.name,
                coordinate=request.origin.coordinate,
                icon="origin",
            )
        ]
        walk_sources = [first_source]

        for index, transit_leg in enumerate(transit_legs, start=1):
            if index > 1:
                previous_leg = transit_legs[index - 2]
                transfer_walk, transfer_source = await self._build_walk(
                    segment_id=f"walk-transfer-{index - 1}",
                    start=previous_leg.alighting_stop,
                    end=transit_leg.boarding_stop,
                    title="Transfer walk",
                    preferences=request.preferences,
                )
                segments.append(transfer_walk.segment)
                markers.extend(transfer_walk.markers)
                walk_sources.append(transfer_source)

            transit_segment = build_transit_segment(transit_leg, index=index)
            segments.append(transit_segment)
            markers.extend(build_transit_stop_markers(transit_leg, transit_segment.id, index))

        last_mile, last_source = await self._build_walk(
            segment_id="walk-last-mile",
            start=transit_legs[-1].alighting_stop,
            end=request.destination,
            title="Shade-first last mile",
            preferences=request.preferences,
        )
        segments.append(last_mile.segment)
        walk_sources.append(last_source)

        markers = [
            *markers,
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
                micro_router=build_micro_router_debug_name(walk_sources),
                tmap_live_sync=self._settings.tmap_use_live
                and self._settings.tmap_app_key is not None,
                note=build_debug_note(walk_sources),
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


def build_micro_router_debug_name(walk_sources: list[str]) -> str:
    if all(source == "pgrouting" for source in walk_sources):
        return "postgis-pgrouting-pedestrian-router"
    if all(source == "demo" for source in walk_sources):
        return "demo-weighted-pedestrian-router"

    return "mixed-pgrouting-demo-pedestrian-router"


def build_debug_note(walk_sources: list[str]) -> str:
    if all(source == "pgrouting" for source in walk_sources):
        return (
            "Route preview uses the PostGIS pedestrian graph "
            "configured by PEDESTRIAN_GRAPH_VERTEX_TABLE and PEDESTRIAN_GRAPH_EDGE_TABLE."
        )

    return (
        "Route preview uses pgRouting when endpoints snap to the MVP graph; "
        "out-of-coverage walking segments fall back to the demo router."
    )


def get_transit_legs(skeleton: TransitSkeleton) -> tuple[TransitLegSkeleton, ...]:
    if skeleton.transit_legs:
        return skeleton.transit_legs

    return (
        TransitLegSkeleton(
            boarding_stop=skeleton.boarding_stop,
            alighting_stop=skeleton.alighting_stop,
            geometry=skeleton.geometry,
            transit=skeleton.transit,
            distance_meters=skeleton.distance_meters,
            duration_seconds=skeleton.duration_seconds,
            render_color=skeleton.render_color,
        ),
    )


def build_transit_segment(skeleton: TransitLegSkeleton, *, index: int = 1) -> RouteSegment:
    return RouteSegment(
        id=f"transit-{index}",
        kind="transit",
        mode=skeleton.transit.mode,
        title=build_transit_segment_title(skeleton),
        geometry=skeleton.geometry,
        render=RenderStyle(color=skeleton.render_color, width=7, pattern="solid"),
        metrics=SegmentMetrics(
            distance_meters=skeleton.distance_meters,
            duration_seconds=skeleton.duration_seconds,
            shade_shelters=0,
            stairs_avoided=0,
        ),
        transit=skeleton.transit,
    )


def build_transit_segment_title(skeleton: TransitLegSkeleton) -> str:
    return (
        f"{transit_mode_label(skeleton.transit.mode)} {skeleton.transit.route_name}: "
        f"{skeleton.boarding_stop.name} -> {skeleton.alighting_stop.name}"
    )


def transit_mode_label(mode: str) -> str:
    if mode == "bus":
        return "버스"
    if mode == "subway":
        return "지하철"

    return "대중교통"


def transit_stop_icon(mode: str) -> str:
    if mode == "bus":
        return "bus-stop"
    if mode == "subway":
        return "subway-stop"

    return "transit-stop"


def build_transit_stop_markers(
    skeleton: TransitLegSkeleton,
    segment_id: str,
    index: int,
) -> list[RouteMarker]:
    mode_label = transit_mode_label(skeleton.transit.mode)
    route_name = skeleton.transit.route_name
    icon = transit_stop_icon(skeleton.transit.mode)
    return [
        RouteMarker(
            id=f"boarding-stop-{index}",
            kind="stop",
            title=f"탑승: {mode_label} {route_name} ({skeleton.boarding_stop.name})",
            coordinate=skeleton.boarding_stop.coordinate,
            segment_id=segment_id,
            icon=icon,
        ),
        RouteMarker(
            id=f"alighting-stop-{index}",
            kind="stop",
            title=f"하차: {mode_label} {route_name} ({skeleton.alighting_stop.name})",
            coordinate=skeleton.alighting_stop.coordinate,
            segment_id=segment_id,
            icon=icon,
        ),
    ]


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
