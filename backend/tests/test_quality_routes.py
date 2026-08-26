from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "UniHR@2026"},
    )
    assert response.status_code == 200


def test_admin_can_browse_versioned_quality_suites():
    client = TestClient(app)
    _login(client, "admin")

    overview = client.get("/api/v1/admin/quality")
    suites = client.get("/api/v1/admin/quality/suites")

    assert overview.status_code == 200
    assert suites.status_code == 200
    ids = {item["id"] for item in suites.json()}
    assert {"routing-core", "capability-smoke", "prompt-variations", "adversarial-core"}.issubset(ids)
    routing = client.get("/api/v1/admin/quality/suites/routing-core/cases")
    assert routing.status_code == 200
    assert len(routing.json()) >= 5
    assert all("expectation" in item for item in routing.json())


def test_ordinary_user_cannot_open_or_run_quality_center():
    client = TestClient(app)
    _login(client, "hr-user")

    assert client.get("/api/v1/admin/quality").status_code == 403
    assert client.post(
        "/api/v1/admin/quality/runs", json={"suite_id": "routing-core"},
    ).status_code == 403
