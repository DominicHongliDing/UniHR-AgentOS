from fastapi.testclient import TestClient

from app.main import app


def test_assistant_turn_exposes_stable_hr_route_contract():
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "hr-user", "password": "UniHR@2026"},
    )
    assert login.status_code == 200
    response = client.post(
        "/api/v1/assistant/turn",
        json={"message": "你好，帮我筛选十份简历"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["interpretation"]["execution_route"] == "hr_workflow"
    assert body["interpretation"]["data_class"] == "restricted_hr"
    assert body["next_action"] == "create_or_continue_case"
