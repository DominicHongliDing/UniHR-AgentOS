import asyncio
from contextlib import asynccontextmanager, suppress
from socket import gethostname
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.assistant import router as assistant_router
from app.api.routes.artifacts import router as artifacts_router
from app.api.routes.auth import router as auth_router
from app.api.routes.admin import router as admin_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.context_operations import router as context_operations_router
from app.api.routes.connections import router as connections_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.feedback import router as feedback_router
from app.api.routes.quality import router as quality_router
from app.api.routes.skills import router as skills_router
from app.api.routes.runs import router as runs_router
from app.api.routes.resources import router as resources_router
from app.api.routes.recruitment import router as recruitment_router
from app.api.routes.workbench import router as workbench_router
from app.api.routes.workflow_cases import router as workflow_cases_router
from app.api.routes.channels import router as channels_router
from app.runtime.container import (
    get_channel_gateway,
    get_quality_operations,
    get_settings,
    get_skill_service,
    get_turn_executions,
    get_workbench,
    reset_container,
)


async def _quality_worker(stop: asyncio.Event) -> None:
    worker_id = f"quality-{gethostname()}-{uuid4().hex[:8]}"
    poll_seconds = get_settings().quality_worker_poll_seconds
    while not stop.is_set():
        try:
            action = await asyncio.to_thread(
                get_channel_gateway().execute_next_action, worker_id,
            )
            if action is not None:
                continue
            completed = await asyncio.to_thread(
                get_quality_operations().execute_next, worker_id,
            )
            if completed is not None:
                continue
        except Exception:
            # The durable lease keeps the job recoverable; readiness remains available
            # so an administrator can inspect the explicit degraded storage state.
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            continue


async def _channel_worker(stop: asyncio.Event) -> None:
    worker_id = f"channel-{gethostname()}-{uuid4().hex[:8]}"
    settings = get_settings()
    while not stop.is_set():
        try:
            completed = await asyncio.to_thread(
                get_channel_gateway().execute_next, worker_id,
            )
            if completed is not None:
                continue
        except Exception:
            pass
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.feishu_channel_poll_seconds,
            )
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail readiness during startup if the durable workflow store is unavailable.
    get_workbench()
    get_turn_executions()
    get_skill_service()
    get_quality_operations()
    quality_stop = asyncio.Event()
    quality_task = asyncio.create_task(_quality_worker(quality_stop))
    channel_stop = asyncio.Event()
    channel_task = (
        asyncio.create_task(_channel_worker(channel_stop))
        if get_settings().feishu_channel_enabled else None
    )
    try:
        yield
    finally:
        quality_stop.set()
        channel_stop.set()
        quality_task.cancel()
        if channel_task is not None:
            channel_task.cancel()
        with suppress(asyncio.CancelledError):
            await quality_task
        if channel_task is not None:
            with suppress(asyncio.CancelledError):
                await channel_task
        reset_container()


app = FastAPI(title="UniHR AgentOS API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5180", "http://localhost:5180"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(assistant_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(feedback_router, prefix="/api/v1")
app.include_router(quality_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(workbench_router, prefix="/api/v1")
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(context_operations_router, prefix="/api/v1")
app.include_router(connections_router, prefix="/api/v1")
app.include_router(artifacts_router, prefix="/api/v1")
app.include_router(knowledge_router, prefix="/api/v1")
app.include_router(skills_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")
app.include_router(resources_router, prefix="/api/v1")
app.include_router(recruitment_router, prefix="/api/v1")
app.include_router(workflow_cases_router, prefix="/api/v1")
app.include_router(channels_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "unihr-server"}
