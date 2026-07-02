from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from steptwin_api.main import create_app


def test_pedestrian_graph_validate_accepts_preferred_graph_shape(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    payload = {
        "name": "hoegi-station-to-kyunghee-medical-center",
        "version": "draft-2026-07-02",
        "vertices": [
            {
                "id": 1,
                "kind": "station_exit",
                "name": "Hoegi Station Exit",
                "coordinate": {"latitude": 37.58945, "longitude": 127.05775},
            },
            {
                "id": 2,
                "kind": "crossing",
                "name": "Olive Young crossing",
                "coordinate": {"latitude": 37.58955, "longitude": 127.05785},
            },
        ],
        "edges": [
            {
                "id": 10,
                "source": 1,
                "target": 2,
                "geometry": [
                    {"latitude": 37.58945, "longitude": 127.05775},
                    {"latitude": 37.58955, "longitude": 127.05785},
                ],
                "distance_meters": 14,
                "shade_score": 0.6,
                "slope_grade": 0.02,
                "crossing_type": "crosswalk",
                "surface_type": "paved",
                "width_meters": 2.5,
                "curb_cut": True,
                "wheelchair_ok": True,
            }
        ],
    }

    with TestClient(create_app()) as client:
        response = client.post("/api/v1/pedestrian-graphs/validate", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["dataset_name"] == "hoegi-station-to-kyunghee-medical-center"
    assert data["summary"]["vertex_count"] == 2
    assert data["summary"]["edge_count"] == 1
    assert data["summary"]["shaded_edge_count"] == 1
    assert data["summary"]["crossing_edge_count"] == 1
    assert data["summary"]["route_ready"] is True


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


def test_pedestrian_graph_import_requires_pgrouting_database(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    payload = {
        "replace_existing": True,
        "dataset": {
            "name": "hoegi-station-to-kyunghee-medical-center",
            "version": "draft-2026-07-02",
            "vertices": [
                {
                    "id": 1,
                    "kind": "station_exit",
                    "coordinate": {"latitude": 37.58945, "longitude": 127.05775},
                },
                {
                    "id": 2,
                    "kind": "hospital_gate",
                    "coordinate": {"latitude": 37.59375, "longitude": 127.05158},
                },
            ],
            "edges": [
                {
                    "id": 10,
                    "source": 1,
                    "target": 2,
                    "geometry": [
                        {"latitude": 37.58945, "longitude": 127.05775},
                        {"latitude": 37.59375, "longitude": 127.05158},
                    ],
                }
            ],
        },
    }

    with TestClient(create_app()) as client:
        response = client.post("/api/v1/pedestrian-graphs/import", json=payload)

    assert response.status_code == 503
    assert "DATABASE_URL is required" in response.json()["detail"]
