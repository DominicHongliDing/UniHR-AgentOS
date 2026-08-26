from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_current_principal
from app.domains.identity import Principal
from app.runtime.container import get_repository, get_settings


router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}/download")
def download_artifact(
    artifact_id: str,
    principal: Principal = Depends(get_current_principal),
) -> FileResponse:
    try:
        artifact = get_repository().get_artifact(artifact_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="artifact_not_found") from exc
    path = Path(artifact.path).resolve()
    data_root = get_settings().data_dir.resolve()
    if not path.is_file() or data_root not in path.parents:
        raise HTTPException(status_code=404, detail="artifact_file_not_found")
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.display_name)


@router.get("/{artifact_id}/previews/{preview_index}")
def preview_artifact(
    artifact_id: str,
    preview_index: int,
    principal: Principal = Depends(get_current_principal),
) -> FileResponse:
    try:
        artifact = get_repository().get_artifact(artifact_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="artifact_not_found") from exc
    harness = dict(artifact.metadata.get("artifact_harness") or {})
    details = dict(harness.get("details") or {})
    preview_files = list(details.get("preview_files") or [])
    if preview_index < 0 or preview_index >= len(preview_files):
        raise HTTPException(status_code=404, detail="artifact_preview_not_found")
    path = Path(str(preview_files[preview_index])).resolve()
    data_root = get_settings().data_dir.resolve()
    if not path.is_file() or data_root not in path.parents:
        raise HTTPException(status_code=404, detail="artifact_preview_file_not_found")
    return FileResponse(path, media_type="application/pdf", filename=f"{Path(artifact.display_name).stem}-预览.pdf")
