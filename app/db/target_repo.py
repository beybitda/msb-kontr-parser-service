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
) src
ON (t.NOMER_KONTRAKTA = src.NOMER_KONTRAKTA)
WHEN MATCHED THEN UPDATE SET
    t.KONTR_DATA_START = src.KONTR_DATA_START,
    t.KONTR_DATA_END   = src.KONTR_DATA_END,
    t.KONTR_STAT       = src.KONTR_STAT,
    t.UPDATED_AT       = SYSDATE
WHERE t.KONTR_STAT IS NULL
"""


def merge_from_staging() -> int:
    """UPDATE_TARGET_TABLE: переносит все записи со STATUS_NAME='DONE'
    из очереди/стейджинга MSB_DB_KONTR_PARSE в целевую витрину
    MSB_DB_GRN_BLANK_MONITOR. Возвращает число обновлённых строк."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_MERGE_TARGET_SQL)
        rows = cur.rowcount
        logger.info("UPDATE_TARGET_TABLE: merged rows=%d", rows)
        return rows
