from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from steptwin_api.main import create_app


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
