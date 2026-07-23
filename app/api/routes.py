from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.schemas import (
    HealthResponse,
    ManyParseRequest,
    ManyParseResponse,
    MergeRequest,
    MergeResponse,
    RunStatusResponse,
    SingleParseRequest,
    SingleParseResponse,
    TaskStatus,
    TriggerRequest,
    TriggerResponse,
    RerunNotFoundRequest,
    RerunNotFoundResponse,
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

    background_tasks.add_task(orchestrator.run, process_run_id, req.business_date, req.merge)
    logger.info(
        "Trigger accepted: process_run_id=%s business_date=%s merge=%s",
        process_run_id, req.business_date, req.merge,
    )
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


@router.post("/parser/parse-many", response_model=ManyParseResponse, dependencies=[Depends(verify_api_key)])
async def parse_many(req: ManyParseRequest) -> ManyParseResponse:
    """Ручной batch-парсинг нескольких контрактов, аналог /parser/parse-one
    для списка: нет GAP_ANALYSIS, нет UPDATE_TARGET_TABLE. Контракты
    заводятся/переиспользуются в MSB_DB_KONTR_PARSE по очереди (как в
    parse_one), затем парсятся одним process_run_id, сгруппированные по
    порталу (см. orchestrator.run_many)."""
    process_run_id = f"SVC-MANY-{uuid.uuid4()}"

    rows: list[GapRow] = []
    for item in req.contracts:
        nomer_norm = normalize_nomer(item.nomer_kontrakta)
        kontr_id = parse_repo.get_or_create_kontr_id(
            nomer=item.nomer_kontrakta,
            nomer_norm=nomer_norm,
            portal=item.naim_portala,
            ord_id=item.ord_id,
            dep_id=item.dep_id,
            process_run_id=process_run_id,
        )
        rows.append(
            GapRow(
                kontr_id=kontr_id,
                nomer_kontrakta=item.nomer_kontrakta,
                nomer_kontrakta_norm=nomer_norm,
                naim_portala=item.naim_portala,
                ord_id=item.ord_id,
                dep_id=item.dep_id,
            )
        )

    try:
        results = await orchestrator.run_many(process_run_id, date.today(), rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ManyParseResponse(
        process_run_id=process_run_id,
        results=[
            SingleParseResponse(
                kontr_id=r.kontr_id,
                status_name=r.status_name.value,
                kontr_data_start=r.kontr_data_start,
                kontr_data_end=r.kontr_data_end,
                kontr_stat=r.kontr_stat,
                parse_source_url=r.parse_source_url,
                error_message=r.error_message,
            )
            for r in results
        ],
    )


@router.post("/parser/merge", response_model=MergeResponse, dependencies=[Depends(verify_api_key)])
async def merge(req: MergeRequest) -> MergeResponse:
    """Ручной/отложенный запуск UPDATE_TARGET_TABLE для process_run_id,
    для которого /parser/trigger вызывался с merge=false (либо merge
    нужно перезапустить отдельно после ручного разбора ошибок)."""
    process_run_id = f"SVC-MERGE-{uuid.uuid4()}"
    rows = orchestrator.run_merge(process_run_id, req.business_date)
    return MergeResponse(process_run_id=process_run_id, rows_merged=rows)


@router.post("/parser/rerun-not-found", response_model=RerunNotFoundResponse, dependencies=[Depends(verify_api_key)])
async def rerun_not_found(req: RerunNotFoundRequest, background_tasks: BackgroundTasks) -> RerunNotFoundResponse:
    """Повторный запуск парсинга для всех NOT_FOUND-записей MSB_DB_KONTR_PARSE."""
    settings = get_settings()
    not_found_count = parse_repo.count_not_found()

    if monitor_service.is_rerun_not_found_running(req.business_date, settings.process_name):
        return RerunNotFoundResponse(
            status="ALREADY_RUNNING",
            process_run_id="",
            not_found_count=not_found_count,
            detail="a rerun-not-found pass is already running for this business_date",
        )

    if not_found_count == 0:
        return RerunNotFoundResponse(
            status="NOTHING_TO_RERUN",
            process_run_id="",
            not_found_count=0,
            detail="no records with STATUS_NAME=NOT_FOUND",
        )

    process_run_id = f"SVC-RERUN-{uuid.uuid4()}"
    background_tasks.add_task(orchestrator.run_rerun_not_found, process_run_id, req.business_date)
    logger.info(
        "Rerun NOT_FOUND accepted: process_run_id=%s business_date=%s count=%d",
        process_run_id, req.business_date, not_found_count,
    )
    return RerunNotFoundResponse(status="ACCEPTED", process_run_id=process_run_id, not_found_count=not_found_count)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()
