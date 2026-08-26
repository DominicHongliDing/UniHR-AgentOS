from fastapi.testclient import TestClient

from app.main import app


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "UniHR@2026"},
    )
    assert response.status_code == 200


def test_admin_can_view_product_capabilities_and_run_route_eval():
    client = TestClient(app)
    _login(client, "admin")

    capabilities = client.get("/api/v1/admin/product-capabilities")
    evaluation = client.post("/api/v1/admin/evaluations/product-help-routing")
    operations = client.get("/api/v1/admin/capability-operations")

    assert capabilities.status_code == 200
    by_id = {item["capability_id"]: item for item in capabilities.json()}
    assert by_id["knowledge.single_file_retry"]["status"] == "implemented_manual"
    assert by_id["knowledge.automatic_failure_retry"]["status"] == "not_implemented"
    assert evaluation.status_code == 200
    assert evaluation.json()["status"] == "passed"
    assert evaluation.json()["passed"] == evaluation.json()["total"] == 4
    assert operations.status_code == 200
    assert operations.json()["summary"]["registered_capabilities"] == 10
    assert any(
        item["capability_id"] == "policy_evidence"
        for item in operations.json()["capabilities"]
    )


def test_ordinary_user_cannot_view_product_capability_governance():
    client = TestClient(app)
    _login(client, "hr-user")

    assert client.get("/api/v1/admin/product-capabilities").status_code == 403
    assert client.post("/api/v1/admin/evaluations/product-help-routing").status_code == 403
    assert client.post("/api/v1/admin/evaluations/query-rewrite").status_code == 403
    assert client.get("/api/v1/admin/capability-operations").status_code == 403
    assert client.post("/api/v1/admin/evaluations/capability-routing").status_code == 403
