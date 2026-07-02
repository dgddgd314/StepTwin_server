from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from steptwin_api.main import create_app


def test_walk_route_optimize_requires_pgrouting_database(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    payload = {
        "start": {
            "name": "Hoegi Station",
            "coordinate": {"latitude": 37.58945, "longitude": 127.05775},
        },
        "end": {
            "name": "Kyung Hee Medical Center",
            "coordinate": {"latitude": 37.59375, "longitude": 127.05158},
        },
        "preferences": {
            "avoid_stairs": True,
            "shade_weight": 0.8,
            "stair_weight": 1,
        },
    }

    with TestClient(create_app()) as client:
        response = client.post("/api/v1/walk-routes/optimize", json=payload)

    assert response.status_code == 503
    assert "DATABASE_URL is required" in response.json()["detail"]
