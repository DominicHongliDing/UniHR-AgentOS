from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.routes import knowledge
from app.main import app


class _Job:
    job_id = "job_test_upload"

    def as_dict(self) -> dict[str, str]:
        return {"job_id": self.job_id, "status": "queued"}


class _Jobs:
    def create(self, **_: object) -> _Job:
        return _Job()


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "UniHR@2026"},
    )
    assert response.status_code == 200


def test_admin_uploads_organization_policy_without_shipping_bundled_policy(
    tmp_path, monkeypatch,
) -> None:
    source_root = tmp_path / "knowledge-source"
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda: SimpleNamespace(
            knowledge_source_root=source_root,
            max_upload_bytes=1024,
        ),
    )
    monkeypatch.setattr(knowledge, "_job_repository", lambda: _Jobs())
    indexed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        knowledge,
        "_run_reindex_job",
        lambda _job, _tenant, _actor, paths=(): indexed.append(paths),
    )

    client = TestClient(app)
    _login(client, "admin")
    response = client.post(
        "/api/v1/knowledge/upload",
        files=[("files", ("员工手册.md", b"# fictional policy", "text/markdown"))],
    )

    assert response.status_code == 202
    body = response.json()
    assert body["uploaded"] == ["tenant_unihr_demo/员工手册.md"]
    assert (source_root / body["uploaded"][0]).read_bytes() == b"# fictional policy"
    assert indexed == [("tenant_unihr_demo/员工手册.md",)]


def test_policy_upload_is_admin_only_and_invalid_batch_writes_nothing(
    tmp_path, monkeypatch,
) -> None:
    source_root = tmp_path / "knowledge-source"
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda: SimpleNamespace(
            knowledge_source_root=source_root,
            max_upload_bytes=1024,
        ),
    )
    monkeypatch.setattr(knowledge, "_job_repository", lambda: _Jobs())

    client = TestClient(app)
    _login(client, "hr-user")
    forbidden = client.post(
        "/api/v1/knowledge/upload",
        files=[("files", ("员工手册.md", b"policy", "text/markdown"))],
    )
    assert forbidden.status_code == 403

    _login(client, "admin")
    invalid = client.post(
        "/api/v1/knowledge/upload",
        files=[
            ("files", ("有效政策.md", b"policy", "text/markdown")),
            ("files", ("危险脚本.exe", b"binary", "application/octet-stream")),
        ],
    )

    assert invalid.status_code == 400
    assert not source_root.exists()
