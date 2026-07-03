from __future__ import annotations

from app.core.config import get_settings
from app.models.dto import GapRow, ParseResult, StatusName
from app.parsers.base import ParserAdapter


class GoszakupParser(ParserAdapter):
    """Адаптер под https://goszakup.gov.kz/ru/registry/contract

    NOT IMPLEMENTED в этой поставке. Сервис отправляется без парсинга —
    здесь заглушка, чтобы весь остальной пайплайн (очередь, мониторинг,
    merge в витрину, ретраи) можно было развернуть и проверить уже сейчас.

    Когда будет готова реальная реализация, нужно:
    1. Проверить в DevTools → Network, есть ли у страницы реестра
       контрактов JSON-эндпоинт для поиска по номеру договора — это
       предпочтительнее прямого парсинга HTML.
    2. Если только рендер на JS — Playwright/Selenium как крайний вариант.
    3. Поиск вести по row.nomer_kontrakta_norm (уже нормализован).
    4. Заполнить kontr_data_start/kontr_data_end/kontr_stat,
       parse_source_url и raw_response (сырой ответ для отладки).
    5. Уважать паузы между запросами и retry с backoff (см. core/config.py:
       request_delay_min_sec/max_sec) — это будет сделано в orchestrator,
       здесь только сам вызов.
    """

    portal_name = "Госзакупки"

    async def parse(self, row: GapRow) -> ParseResult:
        settings = get_settings()
        return ParseResult(
            kontr_id=row.kontr_id,
            status_name=StatusName.ERROR,
            parse_source_url=settings.goszakup_base_url,
            error_message="GoszakupParser not implemented yet",
        )
