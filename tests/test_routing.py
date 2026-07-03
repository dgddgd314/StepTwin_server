from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import steptwin_api.services.routing as routing
from steptwin_api.core.config import Settings, get_settings
from steptwin_api.main import create_app
from steptwin_api.schemas.routing import Coordinate, Place, RoutePreviewRequest, TransitDetails
from steptwin_api.services.macro_routing import DemoMacroRouter, TransitLegSkeleton, TransitSkeleton
from steptwin_api.services.pgrouting_micro_routing import (
    PgRoutingGraphConfig,
    PgRoutingPedestrianRoute,
    PgRoutingSnappedEndpoint,
)


def test_route_preview_returns_renderable_hybrid_segments(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("TMAP_USE_LIVE", "false")
    monkeypatch.setenv("TMAP_APP_KEY", "")
    get_settings.cache_clear()

    request_payload = {
        "origin": {
            "name": "Seoul Station",
            "coordinate": {"latitude": 37.5546788, "longitude": 126.9706069},
        },
        "destination": {
            "name": "Hoegi Station",
            "coordinate": {"latitude": 37.589802, "longitude": 127.057936},
        },
        "preferences": {
            "avoid_stairs": True,
            "shade_weight": 0.9,
            "max_extra_walk_ratio": 0.2,
        },
    }

    with TestClient(create_app()) as client:
        response = client.post("/api/v1/routes/preview", json=request_payload)

    assert response.status_code == 200
    payload = response.json()
    assert payload["debug"]["macro_router"] == "demo-tmap-adapter"
    assert payload["debug"]["tmap_live_sync"] is False
    assert [segment["kind"] for segment in payload["segments"]] == [
        "custom_walk",
        "transit",
        "custom_walk",
    ]
    assert payload["segments"][0]["render"]["pattern"] == "dashed"
    assert payload["segments"][1]["render"]["pattern"] == "solid"
    assert payload["segments"][0]["render"]["color"] != payload["segments"][1]["render"]["color"]
    transit = payload["segments"][1]["transit"]
    assert transit["mode"] == "subway"
    assert transit["subway_line"] == transit["route_name"]
    assert transit["bus_number"] is None
    assert transit["route_name"] == "Demo Transit Line"
    assert transit["boarding_stop"] == "StepTwin Demo Station"
    assert transit["alighting_stop"] == "Sunshade Transfer Stop"
    assert len(payload["segments"][1]["geometry"]) == 5


def test_direct_walk_requires_larger_margin_for_vulnerable_user() -> None:
    healthy_request = RoutePreviewRequest(
        origin=Place(name="Origin", coordinate=Coordinate(latitude=37.0, longitude=127.0)),
        destination=Place(name="Destination", coordinate=Coordinate(latitude=37.01, longitude=127.01)),
        vulnerabilities={
            "speed_vulnerability": 0,
            "turn_vulnerability": 0,
            "strength_vulnerability": 0,
        },
    )
    vulnerable_request = RoutePreviewRequest(
        origin=Place(name="Origin", coordinate=Coordinate(latitude=37.0, longitude=127.0)),
        destination=Place(name="Destination", coordinate=Coordinate(latitude=37.01, longitude=127.01)),
        vulnerabilities={
            "speed_vulnerability": 1,
            "turn_vulnerability": 1,
            "strength_vulnerability": 1,
        },
    )

    assert routing.direct_walk_selection_limit_seconds(
        1000,
        healthy_request.effective_preferences,
    ) == pytest.approx(1000)
    assert routing.direct_walk_selection_limit_seconds(
        1000,
        vulnerable_request.effective_preferences,
    ) == pytest.approx(750)


@pytest.mark.asyncio
async def test_route_preview_preserves_separate_transit_legs_for_android() -> None:
    class MixedTransitRouter:
        def build_transit_skeleton(self, origin: Place, destination: Place) -> TransitSkeleton:
            bus_start = Place(
                name="Bus Start",
                coordinate=Coordinate(latitude=37.57, longitude=127.01),
            )
            bus_end = Place(
                name="Bus End",
                coordinate=Coordinate(latitude=37.571, longitude=127.02),
            )
            subway_start = Place(
                name="Subway Start",
                coordinate=Coordinate(latitude=37.572, longitude=127.021),
            )
            subway_end = Place(
                name="Subway End",
                coordinate=Coordinate(latitude=37.58, longitude=127.04),
            )
            bus_leg = TransitLegSkeleton(
                boarding_stop=bus_start,
                alighting_stop=bus_end,
                geometry=[bus_start.coordinate, bus_end.coordinate],
                transit=TransitDetails(
                    mode="bus",
                    route_name="Blue 2012",
                    bus_number="2012",
                    boarding_stop=bus_start.name,
                    alighting_stop=bus_end.name,
                ),
                distance_meters=1000,
                duration_seconds=300,
                render_color="#0068B7",
            )
            subway_leg = TransitLegSkeleton(
                boarding_stop=subway_start,
                alighting_stop=subway_end,
                geometry=[subway_start.coordinate, subway_end.coordinate],
                transit=TransitDetails(
                    mode="subway",
                    route_name="Line 1",
                    subway_line="Line 1",
                    boarding_stop=subway_start.name,
                    alighting_stop=subway_end.name,
                ),
                distance_meters=3000,
                duration_seconds=600,
                render_color="#0052A4",
            )
            return TransitSkeleton(
                boarding_stop=bus_start,
                alighting_stop=subway_end,
                geometry=[*bus_leg.geometry, *subway_leg.geometry],
                transit=bus_leg.transit,
                distance_meters=4000,
                duration_seconds=900,
                render_color=bus_leg.render_color,
                transit_legs=(bus_leg, subway_leg),
            )

    service = routing.RoutePreviewService(
        macro_router=MixedTransitRouter(),
        settings=Settings(database_url="", tmap_use_live=False),
    )

    response = await service.build_preview(
        RoutePreviewRequest(
            origin=Place(name="Origin", coordinate=Coordinate(latitude=37.569, longitude=127.0)),
            destination=Place(
                name="Destination",
                coordinate=Coordinate(latitude=37.581, longitude=127.041),
            ),
        )
    )

    assert [segment.id for segment in response.segments] == [
        "walk-first-mile",
        "transit-1",
        "walk-transfer-1",
        "transit-2",
        "walk-last-mile",
    ]
    assert [segment.mode for segment in response.segments] == [
        "walk",
        "bus",
        "walk",
        "subway",
        "walk",
    ]
    assert response.segments[1].render.color == "#0068B7"
    assert response.segments[3].render.color == "#0052A4"
    assert response.segments[1].transit is not None
    assert response.segments[3].transit is not None
    assert response.segments[1].transit.bus_number == "2012"
    assert response.segments[1].transit.subway_line is None
    assert response.segments[3].transit.bus_number is None
    assert response.segments[3].transit.subway_line == "Line 1"
    assert response.segments[1].title == "버스 Blue 2012: Bus Start -> Bus End"
    assert response.segments[3].title == "지하철 Line 1: Subway Start -> Subway End"

    stop_markers = [marker for marker in response.markers if marker.kind == "stop"]
    assert [(marker.title, marker.icon) for marker in stop_markers] == [
        ("탑승: 버스 Blue 2012 (Bus Start)", "bus-stop"),
        ("하차: 버스 Blue 2012 (Bus End)", "bus-stop"),
        ("탑승: 지하철 Line 1 (Subway Start)", "subway-stop"),
        ("하차: 지하철 Line 1 (Subway End)", "subway-stop"),
    ]


@pytest.mark.asyncio
async def test_route_preview_does_not_choose_demo_direct_walk_for_main_route() -> None:
    class TwoBusRouter:
        def build_transit_skeleton(self, origin: Place, destination: Place) -> TransitSkeleton:
            bus_start = Place(
                name="Bus Start",
                coordinate=Coordinate(latitude=37.0002, longitude=127.0002),
            )
            transfer = Place(
                name="Transfer",
                coordinate=Coordinate(latitude=37.0004, longitude=127.0004),
            )
            bus_end = Place(
                name="Bus End",
                coordinate=Coordinate(latitude=37.0006, longitude=127.0006),
            )
            first_bus = TransitLegSkeleton(
                boarding_stop=bus_start,
                alighting_stop=transfer,
                geometry=[bus_start.coordinate, transfer.coordinate],
                transit=TransitDetails(
                    mode="bus",
                    route_name="Blue 1",
                    bus_number="1",
                    boarding_stop=bus_start.name,
                    alighting_stop=transfer.name,
                ),
                distance_meters=100,
                duration_seconds=30,
                render_color="#0068B7",
            )
            second_bus = TransitLegSkeleton(
                boarding_stop=transfer,
                alighting_stop=bus_end,
                geometry=[transfer.coordinate, bus_end.coordinate],
                transit=TransitDetails(
                    mode="bus",
                    route_name="Blue 2",
                    bus_number="2",
                    boarding_stop=transfer.name,
                    alighting_stop=bus_end.name,
                ),
                distance_meters=100,
                duration_seconds=30,
                render_color="#0068B7",
            )
            return TransitSkeleton(
                boarding_stop=bus_start,
                alighting_stop=bus_end,
                geometry=[*first_bus.geometry, bus_end.coordinate],
                transit=first_bus.transit,
                distance_meters=200,
                duration_seconds=60,
                render_color="#0068B7",
                transit_legs=(first_bus, second_bus),
            )

    service = routing.RoutePreviewService(
        macro_router=TwoBusRouter(),
        settings=Settings(database_url="", tmap_use_live=False),
    )

    response = await service.build_preview(
        RoutePreviewRequest.model_validate(
            {
                "origin": {
                    "name": "Origin",
                    "coordinate": {"latitude": 37.0, "longitude": 127.0},
                },
                "destination": {
                    "name": "Destination",
                    "coordinate": {"latitude": 37.0008, "longitude": 127.0008},
                },
                "vulnerabilities": {
                    "speed_vulnerability": 0,
                    "turn_vulnerability": 0,
                    "strength_vulnerability": 0,
                },
            }
        )
    )

    assert [segment.id for segment in response.segments] == [
        "walk-first-mile",
        "transit-1",
        "walk-transfer-1",
        "transit-2",
        "walk-last-mile",
    ]
    assert response.summary.transit_distance_meters == 200


@pytest.mark.asyncio
async def test_route_preview_prefers_two_buses_for_moderately_vulnerable_user(
    monkeypatch: MonkeyPatch,
) -> None:
    origin = Place(name="Origin", coordinate=Coordinate(latitude=37.0, longitude=127.0))
    destination = Place(
        name="Destination",
        coordinate=Coordinate(latitude=37.012, longitude=127.012),
    )

    class TwoBusRouter:
        def build_transit_skeleton(self, origin: Place, destination: Place) -> TransitSkeleton:
            bus_start = Place(
                name="Bus Start",
                coordinate=Coordinate(latitude=37.001, longitude=127.001),
            )
            transfer = Place(
                name="Transfer",
                coordinate=Coordinate(latitude=37.006, longitude=127.006),
            )
            bus_end = Place(
                name="Bus End",
                coordinate=Coordinate(latitude=37.011, longitude=127.011),
            )
            first_bus = TransitLegSkeleton(
                boarding_stop=bus_start,
                alighting_stop=transfer,
                geometry=[bus_start.coordinate, transfer.coordinate],
                transit=TransitDetails(
                    mode="bus",
                    route_name="Blue 1",
                    bus_number="1",
                    boarding_stop=bus_start.name,
                    alighting_stop=transfer.name,
                ),
                distance_meters=1000,
                duration_seconds=300,
                render_color="#0068B7",
            )
            second_bus = TransitLegSkeleton(
                boarding_stop=transfer,
                alighting_stop=bus_end,
                geometry=[transfer.coordinate, bus_end.coordinate],
                transit=TransitDetails(
                    mode="bus",
                    route_name="Blue 2",
                    bus_number="2",
                    boarding_stop=transfer.name,
                    alighting_stop=bus_end.name,
                ),
                distance_meters=1000,
                duration_seconds=300,
                render_color="#0068B7",
            )
            return TransitSkeleton(
                boarding_stop=bus_start,
                alighting_stop=bus_end,
                geometry=[origin.coordinate, transfer.coordinate, destination.coordinate],
                transit=first_bus.transit,
                distance_meters=2000,
                duration_seconds=600,
                render_color="#0068B7",
                transit_legs=(first_bus, second_bus),
            )

    @asynccontextmanager
    async def fake_session_context() -> AsyncIterator[object]:
        yield object()

    async def fake_find_pgrouting_walk_route(
        executor: object,
        start: Coordinate,
        end: Coordinate,
        preferences: object,
        *,
        graph_config: PgRoutingGraphConfig,
    ) -> PgRoutingPedestrianRoute:
        is_direct_walk = start == origin.coordinate and end == destination.coordinate
        return PgRoutingPedestrianRoute(
            geometry=(start, end),
            steps=(),
            total_cost_seconds=2200 if is_direct_walk else 60,
            total_distance_meters=2200 if is_direct_walk else 60,
            duration_seconds=2200 if is_direct_walk else 60,
            stairs_count=0,
            shade_shelters=0,
            route_kind="weighted",
            start=PgRoutingSnappedEndpoint(
                vertex_id=1,
                coordinate=start,
                snap_distance_meters=5,
            ),
            end=PgRoutingSnappedEndpoint(
                vertex_id=2,
                coordinate=end,
                snap_distance_meters=5,
            ),
        )

    monkeypatch.setattr(routing, "session_context", fake_session_context)
    monkeypatch.setattr(routing, "find_pgrouting_walk_route", fake_find_pgrouting_walk_route)
    service = routing.RoutePreviewService(
        macro_router=TwoBusRouter(),
        settings=Settings(database_url="postgresql+asyncpg://app:app@127.0.0.1:5432/steptwin"),
    )

    response = await service.build_preview(
        RoutePreviewRequest(
            origin=origin,
            destination=destination,
            vulnerabilities={
                "speed_vulnerability": 0.6,
                "turn_vulnerability": 0.6,
                "strength_vulnerability": 0.6,
            },
        )
    )

    assert [segment.mode for segment in response.segments] == [
        "walk",
        "bus",
        "walk",
        "bus",
        "walk",
    ]


@pytest.mark.asyncio
async def test_route_preview_allows_direct_walk_for_slightly_vulnerable_user(
    monkeypatch: MonkeyPatch,
) -> None:
    origin = Place(name="Origin", coordinate=Coordinate(latitude=37.0, longitude=127.0))
    destination = Place(
        name="Destination",
        coordinate=Coordinate(latitude=37.01, longitude=127.01),
    )

    class SingleBusRouter:
        def build_transit_skeleton(self, origin: Place, destination: Place) -> TransitSkeleton:
            bus_start = Place(
                name="Bus Start",
                coordinate=Coordinate(latitude=37.001, longitude=127.001),
            )
            bus_end = Place(
                name="Bus End",
                coordinate=Coordinate(latitude=37.009, longitude=127.009),
            )
            bus_leg = TransitLegSkeleton(
                boarding_stop=bus_start,
                alighting_stop=bus_end,
                geometry=[bus_start.coordinate, bus_end.coordinate],
                transit=TransitDetails(
                    mode="bus",
                    route_name="Blue 1",
                    bus_number="1",
                    boarding_stop=bus_start.name,
                    alighting_stop=bus_end.name,
                ),
                distance_meters=1000,
                duration_seconds=600,
                render_color="#0068B7",
            )
            return TransitSkeleton(
                boarding_stop=bus_start,
                alighting_stop=bus_end,
                geometry=[bus_start.coordinate, bus_end.coordinate],
                transit=bus_leg.transit,
                distance_meters=1000,
                duration_seconds=600,
                render_color="#0068B7",
                transit_legs=(bus_leg,),
            )

    @asynccontextmanager
    async def fake_session_context() -> AsyncIterator[object]:
        yield object()

    async def fake_find_pgrouting_walk_route(
        executor: object,
        start: Coordinate,
        end: Coordinate,
        preferences: object,
        *,
        graph_config: PgRoutingGraphConfig,
    ) -> PgRoutingPedestrianRoute:
        is_direct_walk = start == origin.coordinate and end == destination.coordinate
        return PgRoutingPedestrianRoute(
            geometry=(start, end),
            steps=(),
            total_cost_seconds=1140 if is_direct_walk else 60,
            total_distance_meters=1140 if is_direct_walk else 60,
            duration_seconds=1140 if is_direct_walk else 60,
            stairs_count=0,
            shade_shelters=0,
            route_kind="weighted",
            start=PgRoutingSnappedEndpoint(
                vertex_id=1,
                coordinate=start,
                snap_distance_meters=5,
            ),
            end=PgRoutingSnappedEndpoint(
                vertex_id=2,
                coordinate=end,
                snap_distance_meters=5,
            ),
        )

    monkeypatch.setattr(routing, "session_context", fake_session_context)
    monkeypatch.setattr(routing, "find_pgrouting_walk_route", fake_find_pgrouting_walk_route)
    service = routing.RoutePreviewService(
        macro_router=SingleBusRouter(),
        settings=Settings(database_url="postgresql+asyncpg://app:app@127.0.0.1:5432/steptwin"),
    )

    response = await service.build_preview(
        RoutePreviewRequest(
            origin=origin,
            destination=destination,
            vulnerabilities={
                "speed_vulnerability": 0.35,
                "turn_vulnerability": 0.35,
                "strength_vulnerability": 0.35,
            },
        )
    )

    assert [segment.id for segment in response.segments] == ["walk-direct"]


@pytest.mark.asyncio
async def test_route_preview_uses_pgrouting_graph_when_database_is_configured(
    monkeypatch: MonkeyPatch,
) -> None:
    graph_configs: list[PgRoutingGraphConfig] = []

    @asynccontextmanager
    async def fake_session_context() -> AsyncIterator[object]:
        yield object()

    async def fake_find_pgrouting_walk_route(
        executor: object,
        start: Coordinate,
        end: Coordinate,
        preferences: object,
        *,
        graph_config: PgRoutingGraphConfig,
    ) -> PgRoutingPedestrianRoute:
        graph_configs.append(graph_config)
        return PgRoutingPedestrianRoute(
            geometry=(start, end),
            steps=(),
            total_cost_seconds=10,
            total_distance_meters=12,
            duration_seconds=60,
            stairs_count=0,
            shade_shelters=0,
            route_kind="weighted",
            start=PgRoutingSnappedEndpoint(
                vertex_id=1,
                coordinate=start,
                snap_distance_meters=5,
            ),
            end=PgRoutingSnappedEndpoint(
                vertex_id=2,
                coordinate=end,
                snap_distance_meters=5,
            ),
        )

    monkeypatch.setattr(routing, "session_context", fake_session_context)
    monkeypatch.setattr(routing, "find_pgrouting_walk_route", fake_find_pgrouting_walk_route)
    service = routing.RoutePreviewService(
        macro_router=DemoMacroRouter(),
        settings=Settings(
            database_url="postgresql+asyncpg://app:app@127.0.0.1:5432/steptwin",
            tmap_use_live=True,
        )
    )

    response = await service.build_preview(
        RoutePreviewRequest(
            origin=Place(
                name="Dongdaemun origin",
                coordinate=Coordinate(latitude=37.57331434835078, longitude=127.02771977755259),
            ),
            destination=Place(
                name="Dongdaemun destination",
                coordinate=Coordinate(latitude=37.57599441150771, longitude=127.02806848927277),
            ),
        )
    )

    assert response.debug.micro_router == "postgis-pgrouting-pedestrian-router"
    assert [segment.id for segment in response.segments] == ["walk-direct"]
    assert [config.vertex_table for config in graph_configs] == [
        "osm_pedestrian_vertices",
        "osm_pedestrian_vertices",
        "osm_pedestrian_vertices",
    ]
    assert [config.edge_table for config in graph_configs] == [
        "osm_pedestrian_edges",
        "osm_pedestrian_edges",
        "osm_pedestrian_edges",
    ]
