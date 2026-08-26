from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Role = Literal["admin", "user"]
ANSWER_HISTORY_MANAGE = "answer_history.manage"
RUN_APPROVE = "run.approve"
KNOWLEDGE_MANAGE = "knowledge.manage"


class UserAccount(BaseModel):
    id: str
    username: str
    display_name: str
    role: Role
    tenant_id: str
    active: bool = True
    created_at: str


class Principal(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: Role
    tenant_id: str
    permissions: list[str] = Field(default_factory=list)

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class LoginResult(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: Principal
