from __future__ import annotations

import logging
from datetime import date

from app.core.config import get_settings
from app.db.connection import get_connection
from app.models.dto import MonitorStatus, ProcessType

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO ANALYST_MSB2.MSB_DB_PROCESS_MONITOR (
    PROCESS_RUN_ID, PROCESS_NAME, TASK_NAME, PROCESS_TYPE,
    TARGET_TABLE, START_TIME, ATTEMPT_NUMBER, STATUS, STATUS_NAME,
    BUSINESS_DATE, INSERTED_AT, UPDATED_AT
) VALUES (
    :process_run_id, :process_name, :task_name, :process_type,
    :target_table, SYSTIMESTAMP, :attempt, 0, 'RUNNING',
    :business_date, SYSTIMESTAMP, SYSTIMESTAMP
) RETURNING RUN_ID INTO :run_id
"""

_UPDATE_SQL = """
UPDATE ANALYST_MSB2.MSB_DB_PROCESS_MONITOR
SET END_TIME = SYSTIMESTAMP,
    DURATION_SECONDS = ROUND(
                   (CAST(SYSTIMESTAMP AS DATE) - CAST(start_time AS DATE)) * 86400
               ),
    STATUS = CASE WHEN :status_name = 'SUCCESS' THEN 1 ELSE 0 END,
    STATUS_NAME = :status_name,
    ROWS_PROCESSED = :rows_processed,
    ERROR_MESSAGE = :error_message,
    EXTRA_INFO = :extra_info,
    UPDATED_AT = SYSTIMESTAMP
WHERE RUN_ID = :run_id
"""

_SELECT_RUN_SQL = """
SELECT TASK_NAME, STATUS_NAME, ROWS_PROCESSED,
       TO_CHAR(START_TIME, 'YYYY-MM-DD"T"HH24:MI:SS') AS START_TIME,
       TO_CHAR(END_TIME, 'YYYY-MM-DD"T"HH24:MI:SS') AS END_TIME,
       ERROR_MESSAGE
FROM ANALYST_MSB2.MSB_DB_PROCESS_MONITOR
WHERE PROCESS_RUN_ID = :process_run_id
  AND PROCESS_TYPE = 'SERVICE'
ORDER BY START_TIME
"""

_ALREADY_RUNNING_SQL = """
SELECT STATUS_NAME, COUNT(*)
FROM ANALYST_MSB2.MSB_DB_PROCESS_MONITOR
WHERE BUSINESS_DATE = :business_date
  AND PROCESS_NAME  = :process_name
  AND PROCESS_TYPE  = 'SERVICE'
GROUP BY STATUS_NAME
"""


def log_start(
    process_run_id: str,
    process_name: str,
    task_name: str,
    target_table: str,
    business_date: date,
    attempt: int = 1,
) -> int:
    """Пишет строку RUNNING и возвращает RUN_ID для последующего log_end."""
    with get_connection() as conn:
        cur = conn.cursor()
        run_id_var = cur.var(int)
        cur.execute(
            _INSERT_SQL,
            {
                "process_run_id": process_run_id,
                "process_name": process_name,
                "task_name": task_name,
                "process_type": ProcessType.SERVICE.value,
                "target_table": target_table,
                "attempt": attempt,
                "business_date": business_date,
                "run_id": run_id_var,
            },
        )
        run_id = int(run_id_var.getvalue()[0])
        logger.info("MONITOR START run_id=%s task=%s process_run_id=%s", run_id, task_name, process_run_id)
        return run_id


def log_end(
    run_id: int,
    status_name: MonitorStatus,
    rows_processed: int | None = None,
    error_message: str | None = None,
    extra_info: str | None = None,
) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            _UPDATE_SQL,
            {
                "run_id": run_id,
                "status_name": status_name.value,
                "rows_processed": rows_processed,
                "error_message": (error_message or "")[:4000] or None,
                "extra_info": extra_info,
            },
        )
        logger.info("MONITOR END run_id=%s status=%s rows=%s", run_id, status_name.value, rows_processed)


def already_running(business_date: date, process_name: str) -> str | None:
    """Возвращает статус, если для этой business_date уже есть строки
    сервиса в состоянии RUNNING или все шаги завершены SUCCESS. Иначе
    None (можно стартовать новый прогон с новым process_run_id)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_ALREADY_RUNNING_SQL, {"business_date": business_date, "process_name": process_name})
        rows = cur.fetchall()
        if not rows:
            return None
        statuses = {r[0] for r in rows}
        if "RUNNING" in statuses:
            return "RUNNING"
        if statuses == {"SUCCESS"}:
            return "SUCCESS"
        return None


def fetch_run_status(process_run_id: str) -> list[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SELECT_RUN_SQL, {"process_run_id": process_run_id})
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
