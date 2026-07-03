from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.dto import GapRow, ParseResult


class ParserAdapter(ABC):
    """Общий интерфейс адаптера под конкретный портал.

    Реализация самого парсинга (goszakup_parser.py / samruk_parser.py)
    сознательно не входит в эту поставку — сервис отправляется с
    работающим пайплайном (trigger -> gap -> stage -> merge -> monitor),
    но с парсерами-заглушками, которые помечают записи как ERROR с
    понятным сообщением. Логика поиска по сайту добавляется отдельным
    PR, когда определится, доступен ли открытый API портала (см. заметки
    в архитектурном документе: сначала DevTools/Network, HTML/Playwright
    как крайний вариант).
    """

    portal_name: str

    @abstractmethod
    async def parse(self, row: GapRow) -> ParseResult:
        """Ищет контракт row.nomer_kontrakta_norm на портале и возвращает
        ParseResult со статусом DONE (нашли), NOT_FOUND (не нашли) или
        ERROR (сбой запроса/парсинга — кандидат на retry)."""
        raise NotImplementedError
