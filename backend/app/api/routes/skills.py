from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.domains.identity import Principal
from app.domains.skills import SkillDryRunRecord, SkillRecord, SkillResourceRecord, SkillVersionRecord
from app.domains.workbench import UploadPayload
from app.runtime.container import get_skill_dry_runs, get_skill_registry, get_skill_service


router = APIRouter(prefix="/skills", tags=["skills"])


class GenerateSkillCommand(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    preferred_name: str = Field(default="", max_length=80)


class ConversationSkillCommand(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)
    preferred_name: str = Field(default="", max_length=80)


class CompleteSkillCommand(BaseModel):
    supplement: str = Field(default="", max_length=8_000)
    defer_missing_resources: bool = True


class UpdateSkillCommand(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=600)
    instructions: str | None = Field(default=None, max_length=12_000)
    trigger_phrases: list[str] | None = Field(default=None, max_length=16)
    capabilities: list[str] | None = Field(default=None, max_length=16)
    enabled: bool | None = None


class RollbackSkillCommand(BaseModel):
    version: int = Field(ge=1)


class SkillApprovalDecision(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1_000)


@router.get("/{skill_id}/dry-runs", response_model=list[SkillDryRunRecord])
def list_skill_dry_runs(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[SkillDryRunRecord]:
    try:
        return get_skill_dry_runs().list(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc


@router.post("/{skill_id}/dry-runs", response_model=SkillDryRunRecord, status_code=status.HTTP_201_CREATED)
async def run_skill_dry_run(
    skill_id: str,
    prompt: str = Form(..., min_length=1, max_length=20_000),
    files: list[UploadFile] = File(default=[]),
    principal: Principal = Depends(get_current_principal),
) -> SkillDryRunRecord:
    try:
        uploads = [
            UploadPayload(
                filename=item.filename or "upload.bin",
                mime_type=item.content_type or "application/octet-stream",
                content=await item.read(),
            )
            for item in files
        ]
        return get_skill_dry_runs().run(skill_id, prompt, uploads, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.get("", response_model=list[SkillRecord])
def list_skills(principal: Principal = Depends(get_current_principal)) -> list[SkillRecord]:
    return get_skill_registry().list(principal)


@router.post("/generate", response_model=SkillRecord, status_code=status.HTTP_201_CREATED)
def generate_skill(
    command: GenerateSkillCommand,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_service().generate(command.prompt, command.preferred_name, principal)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.post("/from-conversation", response_model=SkillRecord, status_code=status.HTTP_201_CREATED)
def create_skill_from_conversation(
    command: ConversationSkillCommand,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_service().from_conversation(
            command.conversation_id, command.preferred_name, principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.post("/import", response_model=SkillRecord, status_code=status.HTTP_201_CREATED)
async def import_skill(
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_service().import_package(
            file.filename or "SKILL.md", await file.read(), principal,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.patch("/{skill_id}", response_model=SkillRecord)
def update_skill(
    skill_id: str,
    command: UpdateSkillCommand,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        if command.enabled is True and not any(
            value is not None for value in (
                command.display_name,
                command.description,
                command.instructions,
                command.trigger_phrases,
                command.capabilities,
            )
        ):
            return get_skill_service().publish(skill_id, principal)
        return get_skill_registry().update(skill_id, principal=principal, **command.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    try:
        get_skill_service().delete(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{skill_id}/validate", response_model=SkillRecord)
def validate_skill(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_service().validate(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.get("/{skill_id}/resources", response_model=list[SkillResourceRecord])
def list_skill_resources(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[SkillResourceRecord]:
    try:
        return get_skill_service().list_resources(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.post("/{skill_id}/resources", response_model=SkillResourceRecord, status_code=status.HTTP_201_CREATED)
async def add_skill_resource(
    skill_id: str,
    kind: Literal["scripts", "references", "assets"] = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
) -> SkillResourceRecord:
    try:
        return get_skill_service().add_resource(
            skill_id,
            kind=kind,
            filename=file.filename or "resource.bin",
            content=await file.read(),
            mime_type=file.content_type or "application/octet-stream",
            principal=principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.get("/{skill_id}/resources/{resource_id}")
def download_skill_resource(
    skill_id: str,
    resource_id: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    try:
        resource, content = get_skill_service().read_resource(skill_id, resource_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_resource_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc
    filename = quote(resource.name)
    return Response(
        content=content,
        media_type=resource.mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.delete("/{skill_id}/resources/{resource_id}", response_model=SkillRecord)
def delete_skill_resource(
    skill_id: str,
    resource_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_service().delete_resource(skill_id, resource_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_resource_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.post("/{skill_id}/complete", response_model=SkillRecord)
def complete_skill_authoring(
    skill_id: str,
    command: CompleteSkillCommand,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_service().complete_authoring(
            skill_id,
            command.supplement,
            defer_missing_resources=command.defer_missing_resources,
            principal=principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.get("/{skill_id}/versions", response_model=list[SkillVersionRecord])
def list_skill_versions(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[SkillVersionRecord]:
    try:
        return get_skill_registry().list_versions(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc


@router.post("/{skill_id}/rollback", response_model=SkillRecord)
def rollback_skill(
    skill_id: str,
    command: RollbackSkillCommand,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_registry().rollback(skill_id, command.version, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_version_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.post("/{skill_id}/organization-request", response_model=SkillRecord)
def request_skill_organization_approval(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_service().request_organization_approval(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)) from exc


@router.get("/organization-approvals/pending", response_model=list[SkillRecord])
def list_skill_organization_approvals(
    principal: Principal = Depends(get_current_principal),
) -> list[SkillRecord]:
    try:
        return get_skill_registry().list_organization_approvals(principal)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/{skill_id}/organization-decision", response_model=SkillRecord)
def decide_skill_organization_approval(
    skill_id: str,
    command: SkillApprovalDecision,
    principal: Principal = Depends(get_current_principal),
) -> SkillRecord:
    try:
        return get_skill_registry().decide_organization_approval(
            skill_id, command.approved, principal, command.comment,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_approval_not_found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/{skill_id}/export-package")
def export_skill_package(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    try:
        item = get_skill_registry().get(skill_id, principal)
        content = get_skill_service().export_package(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    filename = quote(f"{item.name}-v{item.version}.zip")
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/{skill_id}/export", response_class=PlainTextResponse)
def export_skill(
    skill_id: str,
    principal: Principal = Depends(get_current_principal),
) -> PlainTextResponse:
    try:
        item = get_skill_registry().get(skill_id, principal)
        content = get_skill_registry().export_markdown(skill_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    filename = quote(f"{item.name}-v{item.version}-SKILL.md")
    return PlainTextResponse(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
