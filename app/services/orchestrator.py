from __future__ import annotations

import asyncio
import logging
import random
from datetime import date

from app.core.config import get_settings
from app.db import gap_repo, parse_repo, target_repo
from app.models.dto import GapRow, ParseResult, Portal, StatusName
from app.parsers.base import ParserAdapter
from app.parsers.goszakup_parser import GoszakupParser
from app.parsers.samruk_parser import SamrukParser
from app.services.monitor_service import TaskMonitor

logger = logging.getLogger(__name__)

goszakup_parser = GoszakupParser()
samruk_parser = SamrukParser()


async def _parse_and_stage(parser: ParserAdapter, rows: list[GapRow]) -> tuple[dict, list[ParseResult]]:
    """Прогоняет parser.parse() по каждой строке очереди, сразу сохраняет
    результат в MSB_DB_KONTR_PARSE и выдерживает паузу между запросами.
    Возвращает (агрегированные счётчики, список ParseResult в порядке rows)."""
    settings = get_settings()
    counts = {"done": 0, "not_found": 0, "error": 0}
    results: list[ParseResult] = []

    for row in rows:
        parse_repo.mark_in_progress(row.kontr_id)
        try:
            result = await parser.parse(row)
        except Exception as exc:  # noqa: BLE001 — сбой одного контракта не должен ронять весь батч
            logger.exception("Parse failed for kontr_id=%s", row.kontr_id)

            result = ParseResult(kontr_id=row.kontr_id, status_name=StatusName.ERROR, error_message=str(exc))

        final_status = parse_repo.status_or_not_found(result.status_name, row.attempt_number, settings.max_attempts)
        result.status_name = final_status
        parse_repo.save_result(result)
        results.append(result)

        if final_status == StatusName.DONE:
            counts["done"] += 1
        elif final_status == StatusName.NOT_FOUND:
            counts["not_found"] += 1
        else:
            counts["error"] += 1

        await asyncio.sleep(random.uniform(settings.request_delay_min_sec, settings.request_delay_max_sec))

    return counts, results


async def run(process_run_id: str, business_date: date, merge: bool = False) -> None:
    """Оркестрация всего пайплайна для одного process_run_id.
    Каждый шаг = отдельная строка мониторинга (TASK_NAME).
    merge=False пропускает UPDATE_TARGET_TABLE (например, если merge
    нужно прогнать отдельно/позже через /parser/merge)."""
    settings = get_settings()

    with TaskMonitor("GAP_ANALYSIS", process_run_id, business_date, "MSB_DB_KONTR_PARSE") as m:
        gaps = gap_repo.fetch_gaps(process_run_id, settings.max_attempts)
        m.rows_processed = len(gaps)
        m.extra_info = {"total_gaps": len(gaps)}

    samruk_rows = [g for g in gaps if Portal.SAMRUK.value.lower() in g.naim_portala.lower()]
    goszakup_rows = [g for g in gaps if Portal.GOSZAKUP.value.lower() in g.naim_portala.lower()]

    with TaskMonitor("PARSE_SAMRUK", process_run_id, business_date, "MSB_DB_KONTR_PARSE") as m:
        counts, _ = await _parse_and_stage(samruk_parser, samruk_rows)
        m.rows_processed = len(samruk_rows)
        m.extra_info = {"portal": "samruk", **counts}

    with TaskMonitor("PARSE_GOSZAKUP", process_run_id, business_date, "MSB_DB_KONTR_PARSE") as m:
        counts, _ = await _parse_and_stage(goszakup_parser, goszakup_rows)
        m.rows_processed = len(goszakup_rows)
        m.extra_info = {"portal": "goszakup", **counts}

    if merge:
        run_merge(process_run_id, business_date)
    else:
        logger.info("UPDATE_TARGET_TABLE skipped (merge=False) process_run_id=%s", process_run_id)


async def run_single(process_run_id: str, business_date: date, row: GapRow) -> ParseResult:
    """Парсинг одного контракта в обход GAP_ANALYSIS. Один шаг мониторинга,
    результат сразу пишется в MSB_DB_KONTR_PARSE. UPDATE_TARGET_TABLE
    (merge в MSB_DB_GRN_BLANK_MONITOR) сознательно не вызывается."""
    settings = get_settings()
    portal_lower = row.naim_portala.lower()

    if Portal.SAMRUK.value.lower() in portal_lower:
        parser: ParserAdapter = samruk_parser
    elif Portal.GOSZAKUP.value.lower() in portal_lower:
        parser = goszakup_parser
    else:
        raise ValueError(f"Unknown naim_portala: {row.naim_portala!r}")

    with TaskMonitor("PARSE_SINGLE_CONTRACT", process_run_id, business_date, "MSB_DB_KONTR_PARSE") as m:
        parse_repo.mark_in_progress(row.kontr_id)
        try:
            result = await parser.parse(row)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Single parse failed for kontr_id=%s", row.kontr_id)
            result = ParseResult(kontr_id=row.kontr_id, status_name=StatusName.ERROR, error_message=str(exc))

        final_status = parse_repo.status_or_not_found(result.status_name, row.attempt_number, settings.max_attempts)
        result.status_name = final_status
        parse_repo.save_result(result)

        m.rows_processed = 1
        m.extra_info = {"portal": parser.portal_name, "kontr_id": row.kontr_id, "status": final_status.value}

    return result


async def run_many(process_run_id: str, business_date: date, rows: list[GapRow]) -> list[ParseResult]:
    """Batch-версия run_single: парсинг нескольких контрактов в обход
    GAP_ANALYSIS, без UPDATE_TARGET_TABLE. В отличие от run_single, строки
    группируются по порталу и обрабатываются двумя шагами мониторинга
    (PARSE_SAMRUK_MANY / PARSE_GOSZAKUP_MANY) — так и мониторинг остаётся
    по порталам, и внутри каждой группы применяется та же пауза между
    запросами, что и в основном пайплайне (см. _parse_and_stage).
    Результат возвращается в том же порядке, что и входной список rows.

    Портал каждой строки проверяется до старта парсинга: если
    naim_portala не совпадает ни с одним из Portal ни по одной из
    подстрок — поднимается ValueError (см. run_single для той же
    проверки в одиночном варианте)."""
    for row in rows:
        portal_lower = row.naim_portala.lower()
        if Portal.SAMRUK.value.lower() not in portal_lower and Portal.GOSZAKUP.value.lower() not in portal_lower:
            raise ValueError(f"Unknown naim_portala: {row.naim_portala!r}")

    samruk_rows = [g for g in rows if Portal.SAMRUK.value.lower() in g.naim_portala.lower()]
    goszakup_rows = [g for g in rows if Portal.GOSZAKUP.value.lower() in g.naim_portala.lower()]

    all_results: list[ParseResult] = []

    with TaskMonitor("PARSE_SAMRUK_MANY", process_run_id, business_date, "MSB_DB_KONTR_PARSE") as m:
        counts, batch_results = await _parse_and_stage(samruk_parser, samruk_rows)
        all_results.extend(batch_results)
        m.rows_processed = len(samruk_rows)
        m.extra_info = {"portal": "samruk", **counts}

    with TaskMonitor("PARSE_GOSZAKUP_MANY", process_run_id, business_date, "MSB_DB_KONTR_PARSE") as m:
        counts, batch_results = await _parse_and_stage(goszakup_parser, goszakup_rows)
        all_results.extend(batch_results)
        m.rows_processed = len(goszakup_rows)
        m.extra_info = {"portal": "goszakup", **counts}

    by_kontr_id = {r.kontr_id: r for r in all_results}
    return [by_kontr_id[row.kontr_id] for row in rows]


def run_merge(process_run_id: str, business_date: date) -> int:
    """UPDATE_TARGET_TABLE как отдельный, самостоятельно вызываемый шаг —
    используется и из run() (merge=True), и напрямую из /parser/merge."""
    with TaskMonitor("UPDATE_TARGET_TABLE", process_run_id, business_date, "MSB_DB_GRN_BLANK_MONITOR") as m:
        merged = target_repo.merge_from_staging(process_run_id)
        m.rows_processed = merged
    return merged
