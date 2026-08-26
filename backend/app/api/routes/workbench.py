from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_principal
from app.domains.identity import Principal
from app.domains.files import UnsupportedFileError
from app.domains.workbench import UploadPayload
from app.domains.workbench.contracts import WorkbenchTurnResponse
from app.domains.turns import TERMINAL_EXECUTION_STATUSES, TurnExecutionRecord
from app.runtime.container import get_turn_executions, get_workbench


router = APIRouter(prefix="/assistant", tags=["assistant"])


async def _read_turn_input(
    *,
    task_brief_json: str,
    files: list[UploadFile],
) -> tuple[dict[str, object] | None, list[UploadPayload]]:
    try:
        task_brief = json.loads(task_brief_json) if task_brief_json.strip() else None
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="task_brief_invalid_json") from exc
    if task_brief is not None and not isinstance(task_brief, dict):
        raise HTTPException(status_code=400, detail="task_brief_must_be_object")
    uploads = [
        UploadPayload(
            filename=item.filename or "upload",
            mime_type=item.content_type or "application/octet-stream",
            content=await item.read(),
        )
        for item in files
    ]
    return task_brief, uploads


@router.post("/turns:start", response_model=TurnExecutionRecord, status_code=202)
async def start_workbench_turn(
    message: str = Form(default="", max_length=20_000),
    conversation_id: str | None = Form(default=None),
    task_mode: str = Form(default="自动选择"),
    task_brief_json: str = Form(default="", max_length=40_000),
    document_font: str = Form(default="仿宋_GB2312"),
    document_font_size: float = Form(default=16, ge=10.5, le=22),
    files: list[UploadFile] = File(default=[]),
    principal: Principal = Depends(get_current_principal),
) -> TurnExecutionRecord:
    task_brief, uploads = await _read_turn_input(task_brief_json=task_brief_json, files=files)
    return get_turn_executions().start(
        principal=principal,
        message=message,
        conversation_id=conversation_id,
        task_mode=task_mode,
        task_brief=task_brief,
        uploads=uploads,
        document_font=document_font,
        document_font_size=document_font_size,
    )


@router.get("/turns/{execution_id}", response_model=TurnExecutionRecord)
def get_turn_execution(
    execution_id: str,
    principal: Principal = Depends(get_current_principal),
) -> TurnExecutionRecord:
    try:
        return get_turn_executions().get(execution_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="turn_execution_not_found") from exc


@router.post("/turns/{execution_id}/cancel", response_model=TurnExecutionRecord)
def cancel_turn_execution(
    execution_id: str,
    principal: Principal = Depends(get_current_principal),
) -> TurnExecutionRecord:
    try:
        return get_turn_executions().cancel(execution_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="turn_execution_not_found") from exc


@router.post("/turns/{execution_id}/steer", response_model=TurnExecutionRecord, status_code=202)
async def steer_turn_execution(
    execution_id: str,
    message: str = Form(default="", max_length=20_000),
    conversation_id: str | None = Form(default=None),
    task_mode: str = Form(default="自动选择"),
    task_brief_json: str = Form(default="", max_length=40_000),
    document_font: str = Form(default="仿宋_GB2312"),
    document_font_size: float = Form(default=16, ge=10.5, le=22),
    files: list[UploadFile] = File(default=[]),
    principal: Principal = Depends(get_current_principal),
) -> TurnExecutionRecord:
    task_brief, uploads = await _read_turn_input(task_brief_json=task_brief_json, files=files)
    try:
        return get_turn_executions().steer(
            execution_id,
            principal=principal,
            message=message,
            conversation_id=conversation_id,
            task_mode=task_mode,
            task_brief=task_brief,
            uploads=uploads,
            document_font=document_font,
            document_font_size=document_font_size,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="turn_execution_not_found") from exc


@router.get("/turns/{execution_id}/events")
async def stream_turn_execution_events(
    execution_id: str,
    request: Request,
    after: int = 0,
    principal: Principal = Depends(get_current_principal),
) -> StreamingResponse:
    module = get_turn_executions()
    try:
        module.get(execution_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="turn_execution_not_found") from exc

    async def generate() -> AsyncIterator[str]:
        cursor = max(0, after)
        idle_ticks = 0
        while not await request.is_disconnected():
            events = module.events(execution_id, principal, after=cursor)
            for item in events:
                cursor = item.sequence
                payload = item.model_dump(mode="json")
                yield (
                    f"id: {item.sequence}\n"
                    f"event: {item.event}\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
            current = module.get(execution_id, principal)
            if current.status in TERMINAL_EXECUTION_STATUSES and not events:
                break
            idle_ticks += 1
            if idle_ticks % 20 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/turns", response_model=WorkbenchTurnResponse)
async def handle_workbench_turn(
    message: str = Form(default="", max_length=20_000),
    conversation_id: str | None = Form(default=None),
    task_mode: str = Form(default="自动选择"),
    task_brief_json: str = Form(default="", max_length=40_000),
    document_font: str = Form(default="仿宋_GB2312"),
    document_font_size: float = Form(default=16, ge=10.5, le=22),
    files: list[UploadFile] = File(default=[]),
    principal: Principal = Depends(get_current_principal),
) -> WorkbenchTurnResponse:
    task_brief, uploads = await _read_turn_input(task_brief_json=task_brief_json, files=files)
    try:
        return await run_in_threadpool(
            get_workbench().handle_turn,
            message=message,
            conversation_id=conversation_id,
            task_mode=task_mode,
            task_brief=task_brief,
            uploads=uploads,
            document_font=document_font,
            document_font_size=document_font_size,
            principal=principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
