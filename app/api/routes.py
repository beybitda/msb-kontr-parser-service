from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.schemas import (
    HealthResponse,
    RunStatusResponse,
    SingleParseRequest,
    SingleParseResponse,
    TaskStatus,
    TriggerRequest,
    TriggerResponse,
)
from app.core.config import get_settings
from app.core.security import verify_api_key
from app.db import monitor_repo, parse_repo
from app.models.dto import GapRow
from app.services import monitor_service, orchestrator
from app.parsers.normalizer import normalize_nomer

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/parser/trigger", response_model=TriggerResponse, dependencies=[Depends(verify_api_key)])
async def trigger(req: TriggerRequest, background_tasks: BackgroundTasks) -> TriggerResponse:
    """Точка входа от Informatica: post-session success command вызывает
    этот эндпоинт после успешной сборки MSB_DB_GRN_BLANK_MONITOR.
    process_run_id больше не приходит от вызывающей стороны — генерируется
    здесь; идемпотентность теперь по business_date (один прогон пайплайна
    на бизнес-дату)."""
    settings = get_settings()
    if monitor_service.already_running(req.business_date, settings.process_name):
        return TriggerResponse(status="ALREADY_RUNNING", process_run_id="")

    process_run_id = f"SVC-{uuid.uuid4()}"
    if monitor_service.already_succeeded(req.business_date, settings.process_name):
        return TriggerResponse(
            status="ALREADY_SUCCESS",
            process_run_id=process_run_id,
            detail="pipeline already completed successfully for this process_run_id",
        )

    background_tasks.add_task(orchestrator.run, process_run_id, req.business_date)
    logger.info("Trigger accepted: process_run_id=%s business_date=%s", process_run_id, req.business_date)
    return TriggerResponse(status="ACCEPTED", process_run_id=process_run_id)


@router.get("/parser/status/{process_run_id}", response_model=RunStatusResponse, dependencies=[Depends(verify_api_key)])
async def get_status(process_run_id: str) -> RunStatusResponse:
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


@router.post("/parser/parse-one", response_model=SingleParseResponse, dependencies=[Depends(verify_api_key)])
async def parse_one(req: SingleParseRequest) -> SingleParseResponse:
    """Ручной парсинг одного контракта по номеру. В отличие от /parser/trigger:
    - нет GAP_ANALYSIS (номер и портал приходят в запросе);
    - нет UPDATE_TARGET_TABLE (в MSB_DB_GRN_BLANK_MONITOR ничего не пишется);
    - синхронный, один шаг мониторинга PARSE_SINGLE_CONTRACT."""
    process_run_id = f"SVC-SINGLE-{uuid.uuid4()}"
    nomer_norm = normalize_nomer(req.nomer_kontrakta)

    kontr_id = parse_repo.get_or_create_kontr_id(
        nomer=req.nomer_kontrakta,
        nomer_norm=nomer_norm,
        portal=req.naim_portala,
        ord_id=req.ord_id,
        dep_id=req.dep_id,
        process_run_id=process_run_id,
    )

    row = GapRow(
        kontr_id=kontr_id,
        nomer_kontrakta=req.nomer_kontrakta,
        nomer_kontrakta_norm=nomer_norm,
        naim_portala=req.naim_portala,
        ord_id=req.ord_id,
        dep_id=req.dep_id,
    )

    try:
        result = await orchestrator.run_single(process_run_id, date.today(), row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SingleParseResponse(
        kontr_id=result.kontr_id,
        status_name=result.status_name.value,
        kontr_data_start=result.kontr_data_start,
        kontr_data_end=result.kontr_data_end,
        kontr_stat=result.kontr_stat,
        parse_source_url=result.parse_source_url,
        error_message=result.error_message,
    )

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()
