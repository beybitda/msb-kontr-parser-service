import asyncio
from datetime import date

import pytest

from app.models.dto import GapRow, MonitorStatus, ParseResult, StatusName
from app.services import orchestrator


@pytest.fixture(autouse=True)
def patch_db(monkeypatch):
    """Подменяет все обращения к Oracle на in-memory заглушки, чтобы
    прогонять оркестратор без реальной БД."""

    gaps = [
        GapRow(kontr_id=1, nomer_kontrakta="1", nomer_kontrakta_norm="1", naim_portala="Госзакупки", ord_id=10),
        GapRow(kontr_id=2, nomer_kontrakta="2", nomer_kontrakta_norm="2", naim_portala="Самрук-Казына", ord_id=20),
    ]

    monkeypatch.setattr("app.services.orchestrator.gap_repo.fetch_gaps", lambda *a, **k: gaps)
    monkeypatch.setattr("app.services.orchestrator.parse_repo.mark_in_progress", lambda *a, **k: None)
    monkeypatch.setattr("app.services.orchestrator.parse_repo.save_result", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.orchestrator.parse_repo.status_or_not_found",
        lambda status_name, attempt, max_attempts: status_name,
    )
    monkeypatch.setattr("app.services.orchestrator.target_repo.merge_from_staging", lambda *a, **k: 0)

    log_calls = []
    monkeypatch.setattr(
        "app.services.monitor_service.monitor_repo.log_start",
        lambda **k: (log_calls.append(("start", k)), len(log_calls))[1],
    )
    monkeypatch.setattr(
        "app.services.monitor_service.monitor_repo.log_end",
        lambda run_id, status_name, **k: log_calls.append(("end", run_id, status_name)),
    )

    # без реальных сетевых пауз в тесте
    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr("app.services.orchestrator.asyncio.sleep", _no_sleep)

    return log_calls


def test_orchestrator_runs_all_steps_with_stub_parsers(patch_db):
    asyncio.run(orchestrator.run("test-run-1", date(2026, 7, 1)))

    started_tasks = [c[1]["task_name"] for c in patch_db if c[0] == "start"]
    assert started_tasks == ["GAP_ANALYSIS", "PARSE_GOSZAKUP", "PARSE_SAMRUK", "UPDATE_TARGET_TABLE"]

    ended = [c for c in patch_db if c[0] == "end"]
    # заглушки парсеров возвращают ERROR, но сам шаг мониторинга всё равно
    # завершается SUCCESS (ошибка отдельного контракта не роняет задачу)
    assert all(status == MonitorStatus.SUCCESS for _, _, status in ended)
