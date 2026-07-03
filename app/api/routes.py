from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.schemas import HealthResponse, RunStatusResponse, TaskStatus, TriggerRequest, TriggerResponse
from app.core.security import verify_api_key
from app.db import monitor_repo
from app.services import monitor_service, orchestrator

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/parser/trigger", response_model=TriggerResponse, dependencies=[Depends(verify_api_key)])
async def trigger(req: TriggerRequest, background_tasks: BackgroundTasks) -> TriggerResponse:
    """Точка входа от Informatica: post-session success command вызывает
    этот эндпоинт после успешной сборки MSB_DB_GRN_BLANK_MONITOR."""

    if monitor_service.already_running(req.process_run_id):
        return TriggerResponse(status="ALREADY_RUNNING", process_run_id=req.process_run_id)

    if monitor_service.already_succeeded(req.process_run_id):
        return TriggerResponse(
            status="ALREADY_SUCCESS",
            process_run_id=req.process_run_id,
            detail="pipeline already completed successfully for this process_run_id",
        )

    background_tasks.add_task(orchestrator.run, req.process_run_id, req.business_date)
    logger.info("Trigger accepted: process_run_id=%s business_date=%s", req.process_run_id, req.business_date)
    return TriggerResponse(status="ACCEPTED", process_run_id=req.process_run_id)


@router.get("/parser/status/{process_run_id}", response_model=RunStatusResponse, dependencies=[Depends(verify_api_key)])
async def get_status(process_run_id: str) -> RunStatusResponse:
    from app.core.config import get_settings

    settings = get_settings()
    rows = monitor_repo.fetch_run_status(process_run_id)
    tasks = [
        TaskStatus(
            task_name=r["task_name"],
            status_name=r["status_name"],
            rows_processed=r["rows_processed"],
            start_time=r["start_time"],
            end_time=r["end_time"],
            error_message=r["error_message"],
        )
        for r in rows
    ]
    return RunStatusResponse(process_run_id=process_run_id, process_name=settings.process_name, tasks=tasks)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()
