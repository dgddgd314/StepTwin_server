from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from steptwin_api.main import create_app


def test_health_without_database_url(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "")

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "Steptwin API"
    assert payload["environment"] == "test"
    assert payload["checks"]["application"]["status"] == "ok"
    assert payload["checks"]["database"]["status"] == "disabled"
