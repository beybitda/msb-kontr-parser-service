from __future__ import annotations

import logging

from app.db.connection import get_connection

logger = logging.getLogger(__name__)

_MERGE_TARGET_SQL = """
MERGE INTO ANALYST_MSB2.MSB_DB_GRN_BLANK_MONITOR t
USING (
    SELECT NOMER_KONTRAKTA, KONTR_DATA_START, KONTR_DATA_END, KONTR_STAT
    FROM ANALYST_MSB2.MSB_DB_KONTR_PARSE
    WHERE STATUS_NAME = 'DONE'
      AND PROCESS_RUN_ID = :process_run_id
) src
ON (t.NOMER_KONTRAKTA = src.NOMER_KONTRAKTA AND t.KONTR_STAT IS NULL)
WHEN MATCHED THEN UPDATE SET
    t.KONTR_DATA_START = src.KONTR_DATA_START,
    t.KONTR_DATA_END   = src.KONTR_DATA_END,
    t.KONTR_STAT       = src.KONTR_STAT,
    t.LOG_DATE         = SYSDATE
"""


def merge_from_staging(process_run_id: str) -> int:
    """UPDATE_TARGET_TABLE: переносит все записи со STATUS_NAME='DONE'
    из очереди/стейджинга MSB_DB_KONTR_PARSE в целевую витрину
    MSB_DB_GRN_BLANK_MONITOR. Возвращает число обновлённых строк."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_MERGE_TARGET_SQL, {"process_run_id": process_run_id})
        rows = cur.rowcount
        logger.info("UPDATE_TARGET_TABLE: merged rows=%d process_run_id=%s", rows, process_run_id)
        return rows
