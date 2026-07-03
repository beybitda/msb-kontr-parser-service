from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class StatusName(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


class MonitorStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ProcessType(str, Enum):
    INFORMATICA = "INFORMATICA"
    SERVICE = "SERVICE"


class Portal(str, Enum):
    GOSZAKUP = "Гос"
    SAMRUK = "Самрук"


@dataclass(slots=True)
class GapRow:
    """Строка-«дырка»: контракт без KONTR_STAT, взятый из
    MSB_DB_GRN_BLANK_MONITOR и заведённый/переиспользованный в
    MSB_DB_KONTR_PARSE."""

    kontr_id: int
    nomer_kontrakta: str
    nomer_kontrakta_norm: str | None
    naim_portala: str
    ord_id: int | None
    dep_id: int | None
    attempt_number: int = 0


@dataclass(slots=True)
class ParseResult:
    """Результат работы конкретного ParserAdapter по одному контракту."""

    kontr_id: int
    status_name: StatusName
    kontr_data_start: date | None = None
    kontr_data_end: date | None = None
    kontr_stat: str | None = None
    parse_source_url: str | None = None
    raw_response: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class MonitorRecord:
    run_id: int | None
    process_run_id: str
    process_name: str
    task_name: str
    process_type: ProcessType
    target_table: str
    business_date: date
    attempt_number: int = 1
    status_name: MonitorStatus = MonitorStatus.RUNNING
    start_time: datetime | None = None
    rows_processed: int | None = None
    error_message: str | None = None
    extra_info: str | None = None
