from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import date
from typing import Iterator

from app.core.config import get_settings
from app.db import monitor_repo
from app.models.dto import MonitorStatus

logger = logging.getLogger(__name__)


class TaskMonitor:
    """Контекст-менеджер одного шага пайплайна (одна строка в
    MSB_DB_PROCESS_MONITOR). Использование:

        with TaskMonitor("GAP_ANALYSIS", process_run_id, business_date, "MSB_DB_KONTR_PARSE") as m:
            rows = gap_repo.fetch_gaps(...)
            m.rows_processed = len(rows)
            m.extra_info = {"found": len(rows)}
    """

    def __init__(self, task_name: str, process_run_id: str, business_date: date, target_table: str):
        self.task_name = task_name
        self.process_run_id = process_run_id
        self.business_date = business_date
        self.target_table = target_table
        self.rows_processed: int | None = None
        self.extra_info: dict | None = None
        self._run_id: int | None = None

    def __enter__(self) -> "TaskMonitor":
        settings = get_settings()
        self._run_id = monitor_repo.log_start(
            process_run_id=self.process_run_id,
            process_name=settings.process_name,
            task_name=self.task_name,
            target_table=self.target_table,
            business_date=self.business_date,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self._run_id is not None
        if exc is not None:
            logger.exception("Task %s failed", self.task_name)
            monitor_repo.log_end(
                self._run_id,
                MonitorStatus.FAILED,
                rows_processed=self.rows_processed,
                error_message=str(exc),
                extra_info=json.dumps(self.extra_info) if self.extra_info else None,
            )
            # исключение не глушим: одна упавшая задача не должна выглядеть
            # успешной, но и не обязательно рушит остальной пайплайн —
            # решение принимает orchestrator (см. run())
            return False

        monitor_repo.log_end(
            self._run_id,
            MonitorStatus.SUCCESS,
            rows_processed=self.rows_processed,
            extra_info=json.dumps(self.extra_info) if self.extra_info else None,
        )
        return False


def already_running(business_date: date, process_name: str) -> bool:
    return monitor_repo.already_running(business_date, process_name) == "RUNNING"


def is_rerun_not_found_running(business_date: date, process_name: str) -> bool:
    return monitor_repo.is_task_running(business_date, process_name, "RERUN_NOT_FOUND")
