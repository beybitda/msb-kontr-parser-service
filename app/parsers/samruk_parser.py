from __future__ import annotations

from app.core.config import get_settings
from app.models.dto import GapRow, ParseResult, StatusName
from app.parsers.base import ParserAdapter


class SamrukParser(ParserAdapter):
    """Адаптер под https://zakup.sk.kz/#/ext?tabs=contractCard&page=1

    NOT IMPLEMENTED в этой поставке — см. пояснение в goszakup_parser.py.
    Поиск ведётся по «Системный номер договора» (row.nomer_kontrakta_norm).
    """

    portal_name = "Самрук-Казына"

    async def parse(self, row: GapRow) -> ParseResult:
        settings = get_settings()
        return ParseResult(
            kontr_id=row.kontr_id,
            status_name=StatusName.ERROR,
            parse_source_url=settings.samruk_base_url,
            error_message="SamrukParser not implemented yet",
        )
