from __future__ import annotations

import logging

from app.db.connection import get_connection
from app.models.dto import ParseResult, StatusName

logger = logging.getLogger(__name__)

_UPDATE_RESULT_SQL = """
UPDATE ANALYST_MSB2.MSB_DB_KONTR_PARSE
SET STATUS_NAME = :status_name,
    ATTEMPT_NUMBER = ATTEMPT_NUMBER + 1,
    LAST_ATTEMPT_DATE = SYSTIMESTAMP,
    LAST_ERROR = :error_message,
    KONTR_DATA_START = :kontr_data_start,
    KONTR_DATA_END = :kontr_data_end,
    KONTR_STAT = :kontr_stat,
    PARSE_SOURCE_URL = :parse_source_url,
    RAW_RESPONSE = :raw_response,
    UPDATED_AT = SYSTIMESTAMP
WHERE KONTR_ID = :kontr_id
"""

_MARK_IN_PROGRESS_SQL = """
UPDATE ANALYST_MSB2.MSB_DB_KONTR_PARSE
SET STATUS_NAME = 'IN_PROGRESS', UPDATED_AT = SYSTIMESTAMP
WHERE KONTR_ID = :kontr_id
"""


def mark_in_progress(kontr_id: int) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_MARK_IN_PROGRESS_SQL, {"kontr_id": kontr_id})


def save_result(result: ParseResult) -> None:
    """Сохраняет результат парсинга одного контракта: DONE / NOT_FOUND / ERROR.
    ATTEMPT_NUMBER < 5 контролируется на уровне orchestrator/parser перед вызовом:
    после 5 неудачных попыток запись остаётся ERROR/NOT_FOUND и уходит на ручной разбор."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            _UPDATE_RESULT_SQL,
            {
                "kontr_id": result.kontr_id,
                "status_name": result.status_name.value,
                "error_message": (result.error_message or "")[:4000] or None,
                "kontr_data_start": result.kontr_data_start,
                "kontr_data_end": result.kontr_data_end,
                "kontr_stat": result.kontr_stat,
                "parse_source_url": result.parse_source_url,
                "raw_response": result.raw_response,
            },
        )
        logger.info(
            "PARSE_RESULT kontr_id=%s status=%s",
            result.kontr_id,
            result.status_name.value,
        )


def count_by_status(process_run_id: str, portal: str) -> dict[str, int]:
    sql = """
        SELECT STATUS_NAME, COUNT(*)
        FROM ANALYST_MSB2.MSB_DB_KONTR_PARSE
        WHERE PROCESS_RUN_ID = :process_run_id AND NAIM_PORTALA = :portal
        GROUP BY STATUS_NAME
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, {"process_run_id": process_run_id, "portal": portal})
        return dict(cur.fetchall())


class MaxAttemptsExceeded(Exception):
    """Поднимается, когда ATTEMPT_NUMBER для записи достиг лимита —
    запись должна остаться в NOT_FOUND/ERROR и уйти на ручной разбор."""


def status_or_not_found(status_name: StatusName, attempt_number: int, max_attempts: int) -> StatusName:
    if status_name in (StatusName.ERROR, StatusName.NOT_FOUND) and attempt_number + 1 >= max_attempts:
        return StatusName.NOT_FOUND
    return status_name
