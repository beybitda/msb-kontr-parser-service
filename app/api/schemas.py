from datetime import date

from pydantic import BaseModel, Field


class TriggerRequest(BaseModel):
    """Тело запроса от Informatica (post-session success command)."""

    process_run_id: str = Field(..., description="workflow_run_id Informatica, сквозной идентификатор запуска сервиса")
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
