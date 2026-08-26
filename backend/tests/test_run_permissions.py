import pytest
from fastapi import HTTPException

from app.api.routes.runs import RunDecisionCommand, decide_run
from app.domains.identity import Principal


class RepositoryMustNotBeRead:
    def get(self, pointer):
        raise AssertionError("an unauthorized decision must be rejected before reading the run")


def test_ordinary_user_cannot_decide_a_waiting_review_run():
    principal = Principal(
        user_id="usr_normal",
        username="normal",
        display_name="普通用户",
        role="user",
        tenant_id="tenant_demo",
        permissions=[],
    )

    with pytest.raises(HTTPException) as caught:
        decide_run(
            "run_waiting_review",
            RunDecisionCommand(approved=True),
            principal=principal,
            repository=RepositoryMustNotBeRead(),
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == "permission_denied"
