from __future__ import annotations

import logging

from app.db.connection import get_connection
from app.models.dto import GapRow, ParseResult, StatusName

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

_SELECT_BY_NOMER_SQL = """
SELECT KONTR_ID FROM ANALYST_MSB2.MSB_DB_KONTR_PARSE WHERE NOMER_KONTRAKTA = :nomer
"""

_INSERT_SINGLE_SQL = """
INSERT INTO ANALYST_MSB2.MSB_DB_KONTR_PARSE (
    KONTR_ID, NOMER_KONTRAKTA, NOMER_KONTRAKTA_NORM, NAIM_PORTALA, ORD_ID, DEP_ID,
    STATUS_NAME, ATTEMPT_NUMBER, PROCESS_RUN_ID, INSERTED_AT, UPDATED_AT
) VALUES (
    ANALYST_MSB2.SEQ_KONTR_PARSE.NEXTVAL, :nomer, :nomer_norm, :portal, :ord_id, :dep_id,
    'NEW', 0, :process_run_id, SYSTIMESTAMP, SYSTIMESTAMP
) RETURNING KONTR_ID INTO :kontr_id
"""

_REUSE_SINGLE_SQL = """
UPDATE ANALYST_MSB2.MSB_DB_KONTR_PARSE
SET PROCESS_RUN_ID = :process_run_id,
    STATUS_NAME = 'NEW',
    UPDATED_AT = SYSTIMESTAMP
WHERE KONTR_ID = :kontr_id
"""

_COUNT_NOT_FOUND_SQL = """
SELECT COUNT(*) FROM ANALYST_MSB2.MSB_DB_KONTR_PARSE WHERE STATUS_NAME = 'NOT_FOUND'
"""

_SELECT_NOT_FOUND_SQL = """
SELECT KONTR_ID, NOMER_KONTRAKTA, NOMER_KONTRAKTA_NORM, NAIM_PORTALA, ORD_ID, DEP_ID
FROM ANALYST_MSB2.MSB_DB_KONTR_PARSE
WHERE STATUS_NAME = 'NOT_FOUND'
"""

_REQUEUE_NOT_FOUND_SQL = """
UPDATE ANALYST_MSB2.MSB_DB_KONTR_PARSE
SET STATUS_NAME = 'NEW',
    ATTEMPT_NUMBER = 0,
    PROCESS_RUN_ID = :process_run_id,
    UPDATED_AT = SYSTIMESTAMP
WHERE STATUS_NAME = 'NOT_FOUND'
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


def get_or_create_kontr_id(
    nomer: str,
    nomer_norm: str | None,
    portal: str,
    ord_id: int | None,
    dep_id: int | None,
    process_run_id: str,
) -> int:
    """Для ручного /parser/parse-one: переиспользует существующую строку
    по NOMER_KONTRAKTA (сбрасывая STATUS_NAME в NEW) либо заводит новую.
    Без GAP_ANALYSIS и без ATTEMPT_NUMBER-лимитов — это ручной триггер."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SELECT_BY_NOMER_SQL, {"nomer": nomer})
        row = cur.fetchone()
        if row:
            kontr_id = int(row[0])
            cur.execute(_REUSE_SINGLE_SQL, {"process_run_id": process_run_id, "kontr_id": kontr_id})
            logger.info("PARSE_SINGLE reuse kontr_id=%s nomer=%s", kontr_id, nomer)
            return kontr_id

        kontr_id_var = cur.var(int)
        cur.execute(
            _INSERT_SINGLE_SQL,
            {
                "nomer": nomer,
                "nomer_norm": nomer_norm,
                "portal": portal,
                "ord_id": ord_id,
                "dep_id": dep_id,
                "process_run_id": process_run_id,
                "kontr_id": kontr_id_var,
            },
        )
        kontr_id = int(kontr_id_var.getvalue()[0])
        logger.info("PARSE_SINGLE created kontr_id=%s nomer=%s", kontr_id, nomer)
        return kontr_id


def count_not_found() -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_COUNT_NOT_FOUND_SQL)
        return int(cur.fetchone()[0])


def fetch_and_requeue_not_found(process_run_id: str) -> list[GapRow]:
    """Забирает все записи STATUS_NAME='NOT_FOUND', атомарно сбрасывает их
    в NEW/ATTEMPT_NUMBER=0 под текущим process_run_id и возвращает как
    GapRow для повторного парсинга. SELECT и UPDATE выполняются в одной
    транзакции get_connection(), но без FOR UPDATE — при параллельном
    вызове /parser/rerun-not-found возможна гонка (см. already_running
    проверку в эндпоинте, которая должна её предотвращать на уровне API)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SELECT_NOT_FOUND_SQL)
        rows = cur.fetchall()
        if not rows:
            return []

        cur.execute(_REQUEUE_NOT_FOUND_SQL, {"process_run_id": process_run_id})
        logger.info("RERUN_NOT_FOUND requeued=%d process_run_id=%s", len(rows), process_run_id)

        return [
            GapRow(
                kontr_id=kontr_id,
                nomer_kontrakta=nomer,
                nomer_kontrakta_norm=nomer_norm,
                naim_portala=naim_portala,
                ord_id=ord_id,
                dep_id=dep_id,
                attempt_number=0,
            )
            for kontr_id, nomer, nomer_norm, naim_portala, ord_id, dep_id in rows
        ]
