from datetime import date, datetime

from pydantic import BaseModel, Field


class TriggerRequest(BaseModel):
    """Тело запроса от Informatica (post-session success command)."""

    business_date: date = Field(..., description="бизнес-дата загрузки витрины")
    target_table: str = Field(default="MSB_DB_GRN_BLANK_MONITOR")


class TriggerResponse(BaseModel):
    status: str  # ACCEPTED | ALREADY_RUNNING
    process_run_id: str
    detail: str | None = None


class TaskStatus(BaseModel):
    task_name: str
    status_name: str
    rows_processed: int | None = None
    start_time: str | None = None
    end_time: str | None = None
    error_message: str | None = None


class RunStatusResponse(BaseModel):
    process_run_id: str
    process_name: str
    tasks: list[TaskStatus]


class HealthResponse(BaseModel):
    status: str = "ok"


class SingleParseRequest(BaseModel):
    """Ручной запуск парсинга одного контракта, без GAP_ANALYSIS и без
    последующего merge в витрину."""

    nomer_kontrakta: str
    naim_portala: str  # должно содержать "Гос" или "Самрук" (см. Portal enum)
    ord_id: int | None = None
    dep_id: int | None = None


class SingleParseResponse(BaseModel):
    kontr_id: int
    status_name: str
    kontr_data_start: datetime | None = None
    kontr_data_end: datetime | None = None
    kontr_stat: str | None = None
    parse_source_url: str | None = None
    error_message: str | None = None
