from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse

from app.api.deps import get_current_principal
from app.domains.identity import Principal
from app.domains.recruitment import (
    RecruitmentCaseAction,
    RecruitmentCaseRecord,
    RecruitmentFolderCreate,
    RecruitmentFolderRecord,
    RecruitmentFolderUpdate,
    RecruitmentInterviewerRecord,
    RecruitmentPackageCreate,
    RecruitmentPackagePreview,
    RecruitmentPackageRecord,
)
from app.runtime.container import get_recruitment_cases, get_recruitment_packages


router = APIRouter(prefix="/recruitment", tags=["recruitment"])


@router.get("/interviewers", response_model=list[RecruitmentInterviewerRecord])
def list_recruitment_interviewers(
    principal: Principal = Depends(get_current_principal),
) -> list[RecruitmentInterviewerRecord]:
    return get_recruitment_cases().list_interviewers(principal)


@router.get("/cases", response_model=list[RecruitmentCaseRecord])
def list_recruitment_cases(
    status: str = "",
    folder_id: str = "",
    query: str = "",
    archived: bool = False,
    principal: Principal = Depends(get_current_principal),
) -> list[RecruitmentCaseRecord]:
    try:
        return get_recruitment_cases().list(
            principal, status=status, folder_id=folder_id, query=query, archived=archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/cases/{case_id}", response_model=RecruitmentCaseRecord)
def get_recruitment_case(
    case_id: str,
    principal: Principal = Depends(get_current_principal),
) -> RecruitmentCaseRecord:
    try:
        return get_recruitment_cases().get(case_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_case_not_found") from exc


@router.post("/cases/{case_id}/actions", response_model=RecruitmentCaseRecord)
def apply_recruitment_case_action(
    case_id: str,
    command: RecruitmentCaseAction,
    principal: Principal = Depends(get_current_principal),
) -> RecruitmentCaseRecord:
    try:
        return get_recruitment_cases().apply(case_id, command, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_case_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/cases/{case_id}/interviews/{session_id}.ics")
def download_interview_calendar_event(
    case_id: str,
    session_id: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    try:
        filename, content = get_recruitment_cases().calendar_event(case_id, session_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_case_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/cases/{case_id}/packages/preview", response_model=RecruitmentPackagePreview)
def preview_recruitment_package(
    case_id: str,
    package_type: str,
    principal: Principal = Depends(get_current_principal),
) -> RecruitmentPackagePreview:
    if package_type not in {"interview_evaluation", "final_decision"}:
        raise HTTPException(status_code=400, detail="recruitment_package_type_invalid")
    try:
        return get_recruitment_packages().preview(case_id, package_type, principal)  # type: ignore[arg-type]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_case_not_found") from exc


@router.get("/cases/{case_id}/packages", response_model=list[RecruitmentPackageRecord])
def list_recruitment_packages(
    case_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[RecruitmentPackageRecord]:
    try:
        return get_recruitment_packages().list(case_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_case_not_found") from exc


@router.post("/cases/{case_id}/packages", response_model=RecruitmentPackageRecord, status_code=201)
def generate_recruitment_package(
    case_id: str,
    command: RecruitmentPackageCreate,
    principal: Principal = Depends(get_current_principal),
) -> RecruitmentPackageRecord:
    try:
        return get_recruitment_packages().generate(case_id, command.package_type, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_case_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/cases/{case_id}/packages/{package_id}/download")
def download_recruitment_package(
    case_id: str,
    package_id: str,
    format: str,
    principal: Principal = Depends(get_current_principal),
) -> FileResponse:
    try:
        path, mime_type, filename = get_recruitment_packages().download(
            case_id, package_id, format, principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_package_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime_type, filename=filename)


@router.get("/folders", response_model=list[RecruitmentFolderRecord])
def list_recruitment_folders(
    principal: Principal = Depends(get_current_principal),
) -> list[RecruitmentFolderRecord]:
    return get_recruitment_cases().list_folders(principal)


@router.post("/folders", response_model=RecruitmentFolderRecord, status_code=201)
def create_recruitment_folder(
    command: RecruitmentFolderCreate,
    principal: Principal = Depends(get_current_principal),
) -> RecruitmentFolderRecord:
    try:
        return get_recruitment_cases().create_folder(command.name, principal)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/folders/{folder_id}", response_model=RecruitmentFolderRecord)
def rename_recruitment_folder(
    folder_id: str,
    command: RecruitmentFolderUpdate,
    principal: Principal = Depends(get_current_principal),
) -> RecruitmentFolderRecord:
    try:
        return get_recruitment_cases().rename_folder(folder_id, command.name, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_folder_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/folders/{folder_id}", status_code=204)
def delete_recruitment_folder(
    folder_id: str,
    principal: Principal = Depends(get_current_principal),
) -> None:
    try:
        get_recruitment_cases().delete_folder(folder_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recruitment_folder_not_found") from exc
