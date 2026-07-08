from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import List
from urllib.parse import quote

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from app.core.config import get_settings
from app.models.dto import GapRow, ParseResult, StatusName
from app.parsers.base import ParserAdapter

logger = logging.getLogger(__name__)

# Попап карточки договора: #/ext(popup:item/5453559047/contractCard)?tabs=contractCard&cid=5453559047&page=1
_CID_RE = re.compile(r"item/(\d+)/contractCard")

_TITLE_RE = re.compile(
    r"(Основной договор|Дополнительное соглашение)\s*№\s*(?P<num>\S+)\s*от\s*(?P<date>\d{2}\.\d{2}\.\d{4})"
)
_END_DATE_RE = re.compile(r"Срок действия договора\s*[:\-]?\s*(\d{2}\.\d{2}\.\d{4})")
_STATUS_RE = re.compile(r"Статус(?: договора)?\s*[:\-]?\s*([^\n]+)")

_MAIN_CONTRACT_TYPE = "Основной договор"


def _parse_dmy(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y")
    except ValueError:
        logger.warning("Не удалось распарсить дату samruk: %r", value)
        return None


class SamrukParser(ParserAdapter):
    """Адаптер под https://zakup.sk.kz — Angular SPA, hash-роутинг,
    открытого JSON API нет (в отличие от goszakup), поэтому парсинг идёт
    headless-браузером (Playwright), а не httpx/BeautifulSoup.

    1. Поиск по системному номеру (row.nomer_kontrakta_norm):
       GET https://zakup.sk.kz/#/ext?tabs=contractCard&sn=<номер>&page=1
       -> cid одного или нескольких договоров (основной + доп. соглашения).
    2. Карточка договора (попап):
       GET https://zakup.sk.kz/#/ext(popup:item/<cid>/contractCard)?tabs=contractCard&cid=<cid>&page=1

    Поля вытаскиваются регулярками из отрендеренного текста страницы
    (устойчивых CSS-селекторов не знаем — у Angular хэшированные классы):
      - "Основной договор № <N> от <DD.MM.YYYY>" / "Дополнительное
        соглашение № ... от ..." -> тип + KONTR_DATA_START
      - "Срок действия договора" -> KONTR_DATA_END
      - "Статус" -> KONTR_STAT

    Как и в goszakup: приоритет у "Основной договор", иначе — самый
    свежий по дате начала. Весь список найденных договоров сохраняется
    в RAW_RESPONSE.
    """

    portal_name = "Самрук-Казына"

    _SEARCH_URL_TMPL = "https://zakup.sk.kz/#/ext?tabs=contractCard&sn={sn}&page=1"
    _DETAIL_URL_TMPL = (
        "https://zakup.sk.kz/#/ext(popup:item/{cid}/contractCard)?tabs=contractCard&cid={cid}&page=1"
    )

    def __init__(self) -> None:
        self._settings = get_settings()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _new_context(self) -> BrowserContext:
        browser = await self._ensure_browser()
        return await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            )
        )

    async def _search(self, nomer_kontrakta_norm: str) -> list[int]:
        """Возвращает cid договоров, найденных по системному номеру
        (в порядке появления на странице)."""
        timeout_ms = self._settings.request_timeout_sec * 1000
        url = self._SEARCH_URL_TMPL.format(sn=quote(nomer_kontrakta_norm, safe=""))
        context = await self._new_context()

        ids: list[int] = []
        seen: set[int] = set()
        try:
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms)
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            # small delay to allow SPA lists render
            await page.wait_for_timeout(1500)

            item_nums: List[str] = await page.locator(".m-found-item__num").evaluate_all("els => els.map(e => e.innerText || '')")
            for t in item_nums:
                mm = re.search(r"([0-9]{6,})", t)
                cid = mm.group(1)
                if mm and cid not in seen:
                    seen.add(cid)
                    ids.append(cid)

        except Exception as exc:
            logger.exception("Samruk search error for nomer_kontrakta_norm=%s", nomer_kontrakta_norm)
            raise ValueError(f"Playwright error during search: {exc}") from exc
        finally:
            await context.close()
            
        return ids

    async def _fetch_detail(self, cid: int) -> dict:
        """Открывает попап карточки договора и вытаскивает поля из
        отрендеренного текста страницы."""
        timeout_ms = self._settings.request_timeout_sec * 1000
        url = self._DETAIL_URL_TMPL.format(cid=cid)
        context = await self._new_context()
        try:
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms)
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            text = await page.inner_text("body")
        finally:
            await context.close()

        title_match = _TITLE_RE.search(text)
        end_match = _END_DATE_RE.search(text)
        status_match = _STATUS_RE.search(text)

        return {
            "id": cid,
            "url": url,
            "type": title_match.group(1) if title_match else None,
            "kontr_data_start": _parse_dmy(title_match.group("date")) if title_match else None,
            "kontr_data_end": _parse_dmy(end_match.group(1)) if end_match else None,
            "kontr_stat": status_match.group(1).strip() if status_match else None,
        }

    @staticmethod
    def _pick_primary(details: list[dict]) -> dict:
        main = next((d for d in details if d["type"] == _MAIN_CONTRACT_TYPE), None)
        if main is not None:
            return main
        return max(details, key=lambda d: d["kontr_data_start"] or datetime.min)

    @staticmethod
    def _serialize_details(details: list[dict]) -> str:
        serializable = []
        for d in details:
            item = dict(d)
            item["kontr_data_start"] = d["kontr_data_start"].isoformat() if d["kontr_data_start"] else None
            item["kontr_data_end"] = d["kontr_data_end"].isoformat() if d["kontr_data_end"] else None
            serializable.append(item)
        return json.dumps(serializable, ensure_ascii=False)

    async def parse(self, row: GapRow) -> ParseResult:
        search_term = row.nomer_kontrakta_norm or row.nomer_kontrakta
        ids: list[int] = []
        try:
            ids = await self._search(search_term)
            if not ids:
                return ParseResult(
                    kontr_id=row.kontr_id,
                    status_name=StatusName.NOT_FOUND,
                    parse_source_url=self._SEARCH_URL_TMPL.format(sn=quote(search_term, safe="")),
                    error_message="Контракт не найден на zakup.sk.kz по системному номеру",
                )

            details = [await self._fetch_detail(cid) for cid in ids]
            primary = self._pick_primary(details)

            return ParseResult(
                kontr_id=row.kontr_id,
                status_name=StatusName.DONE,
                kontr_data_start=primary["kontr_data_start"],
                kontr_data_end=primary["kontr_data_end"],
                kontr_stat=primary["kontr_stat"],
                parse_source_url=primary["url"],
                raw_response=self._serialize_details(details),
            )
        except Exception as exc:  # noqa: BLE001 — Playwright: timeout/navigation/etc.
            logger.exception("Samruk parse error for kontr_id=%s", row.kontr_id)
            return ParseResult(
                kontr_id=row.kontr_id,
                status_name=StatusName.ERROR,
                parse_source_url=self._DETAIL_URL_TMPL.format(cid=ids[0]) if ids else None,
                error_message=f"Playwright error: {exc}",
            )