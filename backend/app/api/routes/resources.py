from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_current_principal
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.domains.resources import (
    ResourceExcerpt,
    ResourceItem,
    ResourceLocator,
    ResourceWorkspace,
    ResourceWorkspaceManifest,
)
from app.runtime.container import get_repository, get_settings


router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("/conversations/{conversation_id}", response_model=ResourceWorkspaceManifest)
def get_resource_workspace(
    conversation_id: str,
    include_archived: bool = False,
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> ResourceWorkspaceManifest:
    try:
        return ResourceWorkspace(repository).build(
            conversation_id, principal, include_archived=include_archived,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc


@router.post("/{resource_id}/query", response_model=ResourceExcerpt)
def query_resource(
    resource_id: str,
    locator: ResourceLocator,
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> ResourceExcerpt:
    try:
        return ResourceWorkspace(repository).query(resource_id, locator, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="resource_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{resource_id}/archive", response_model=ResourceItem)
def archive_resource(
    resource_id: str,
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> ResourceItem:
    try:
        item = ResourceWorkspace(repository).set_archived(resource_id, True, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="resource_not_found") from exc
    return item


@router.post("/{resource_id}/restore", response_model=ResourceItem)
def restore_resource(
    resource_id: str,
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> ResourceItem:
    try:
        item = ResourceWorkspace(repository).set_archived(resource_id, False, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="resource_not_found") from exc
    return item


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> FileResponse:
    try:
        attachment = repository.get_attachment(attachment_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="attachment_not_found") from exc
    path = Path(attachment.saved_path).resolve()
    data_root = get_settings().data_dir.resolve()
    if not path.is_file() or data_root not in path.parents:
        raise HTTPException(status_code=404, detail="attachment_file_not_found")
    return FileResponse(path, media_type=attachment.mime_type, filename=attachment.display_name)
