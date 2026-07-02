from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import steptwin_api.services.routing as routing
from steptwin_api.core.config import Settings
from steptwin_api.main import create_app
from steptwin_api.schemas.routing import Coordinate, Place, RoutePreviewRequest
from steptwin_api.services.pgrouting_micro_routing import (
    PgRoutingGraphConfig,
    PgRoutingPedestrianRoute,
    PgRoutingSnappedEndpoint,
)


def test_route_preview_returns_renderable_hybrid_segments(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "")

    request_payload = {
        "origin": {
            "name": "Seoul City Hall",
            "coordinate": {"latitude": 37.5665, "longitude": 126.9780},
        },
        "destination": {
            "name": "Namsan Seoul Tower",
            "coordinate": {"latitude": 37.5512, "longitude": 126.9882},
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
    assert payload["summary"]["shade_shelters"] == 4
    assert payload["summary"]["stairs_avoided"] == 2
    assert [segment["kind"] for segment in payload["segments"]] == [
        "custom_walk",
        "transit",
        "custom_walk",
    ]
    assert payload["segments"][0]["render"]["pattern"] == "dashed"
    assert payload["segments"][1]["render"]["pattern"] == "solid"
    assert any(marker["kind"] == "shade_shelter" for marker in payload["markers"])


@pytest.mark.asyncio
async def test_route_preview_uses_pgrouting_graph_when_database_is_configured(
    monkeypatch: MonkeyPatch,
) -> None:
    graph_configs: list[PgRoutingGraphConfig] = []

    @asynccontextmanager
    async def fake_session_context():
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
        settings=Settings(
            database_url="postgresql+asyncpg://app:app@127.0.0.1:5432/steptwin",
            pedestrian_graph_vertex_table="pedestrian_vertices",
            pedestrian_graph_edge_table="pedestrian_edges",
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
    assert [config.vertex_table for config in graph_configs] == [
        "pedestrian_vertices",
        "pedestrian_vertices",
    ]
    assert [config.edge_table for config in graph_configs] == [
        "pedestrian_edges",
        "pedestrian_edges",
    ]
