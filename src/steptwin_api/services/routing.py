from uuid import uuid4

from steptwin_api.core.config import Settings, get_settings
from steptwin_api.schemas.routing import (
    RenderStyle,
    RouteDebug,
    RouteMarker,
    RoutePreviewRequest,
    RoutePreviewResponse,
    RouteSegment,
    RouteSummary,
    SegmentMetrics,
    Viewport,
)
from steptwin_api.services.geometry import viewport_for
from steptwin_api.services.macro_routing import DemoMacroRouter, TmapMacroRouter, TransitSkeleton
from steptwin_api.services.micro_routing import DemoMicroRouter


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

    def build_preview(self, request: RoutePreviewRequest) -> RoutePreviewResponse:
        skeleton = self._macro_router.build_transit_skeleton(request.origin, request.destination)

        first_mile = self._micro_router.build_custom_walk(
            segment_id="walk-first-mile",
            start=request.origin,
            end=skeleton.boarding_stop,
            title="Stair-minimized first mile",
            preferences=request.preferences,
        )
        last_mile = self._micro_router.build_custom_walk(
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
                micro_router="ga-weighted-pedestrian-router",
                tmap_live_sync=self._settings.tmap_use_live
                and self._settings.tmap_app_key is not None,
                note="Route preview combines TMAP transit with weighted pedestrian routing.",
            ),
        )


def build_macro_router(settings: Settings) -> DemoMacroRouter | TmapMacroRouter:
    if settings.tmap_use_live:
        return TmapMacroRouter(settings=settings)

    return DemoMacroRouter()


def get_macro_router_name(settings: Settings) -> str:
    if settings.tmap_use_live:
        return "live-tmap-adapter"

    return "demo-tmap-adapter"


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
