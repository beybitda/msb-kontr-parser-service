from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from urllib.parse import urlencode

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.core.config import get_settings
from app.models.dto import GapRow, ParseResult, StatusName
from app.parsers.base import ParserAdapter

logger = logging.getLogger(__name__)

# Попап карточки договора: #/ext(popup:item/5453559047/contractCard)?tabs=contractCard&cid=5453559047&page=1
_CID_RE = re.compile(r"item/(\d+)/contractCard")

_TITLE_RE = re.compile(
    r"(?P<type>[^\n№]+?)\s*№\s*(?P<num>\S+)\s*от\s*(?P<date>\d{2}\.\d{2}\.\d{4})"
)
_END_DATE_RE = re.compile(r"Срок действия договора\s*[:\-]?\s*(\d{2}\.\d{2}\.\d{4})")
_STATUS_RE = re.compile(r"Статус(?: договора)?\s*[:\-]?\s*([^\n]+)")

_MAIN_CONTRACT_TYPE = "Основной договор"

# По умолчанию портал в поиске (без ccs) отдаёт только договоры со
# статусом SIGNED (Заключен). Портал принимает только ОДИН ccs за
# запрос (не список) — поэтому если по номеру ничего не нашлось в
# дефолтном статусе, остальные статусы запрашиваются отдельными
# запросами параллельно (см. _search) и объединяются.
_FALLBACK_STATUSES = [
    "RESCIND",
    "EXECUTED",
    "SUPPLEMENTARY_AGREEMENT",
    "REFUSAL_PERFORM_CONTRACT",
]

# Селектор попапа карточки — из рабочего скрапера (scraper_card.py):
# Angular Material диалог / модалка / кастомный компонент карточки.
_POPUP_SELECTOR = ".mat-dialog-container, .modal-content, app-item-details"
_SPINNER_SELECTOR = ".mat-progress-spinner, .loading-indicator"

_NAV_MAX_RETRIES = 3
_POST_NAV_WAIT_MS = 5000


def _parse_dmy(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y")
    except ValueError:
        logger.warning("Не удалось распарсить дату samruk: %r", value)
        return None


class SamrukParser(ParserAdapter):
    """Адаптер под https://zakup.sk.kz — Angular SPA, hash-роутинг, открытого
    JSON API нет, поэтому парсинг идёт headless-браузером (Playwright).

    Важно (см. разбор рабочего скрапера terequelll/zakup-sk-scraper):
    страница НИКОГДА не доходит до networkidle (постоянные фоновые
    запросы/сокеты у Angular-приложения) — если ждать networkidle после
    goto, он просто таймаутит и мы читаем страницу до того, как данные
    отрисовались. Поэтому здесь используется тот же паттерн, что и в
    рабочем скрапере: goto(wait_until="domcontentloaded") + фиксированная
    пауза + явное ожидание попапа/спиннера, с ретраями.

    1. Поиск по системному номеру (row.nomer_kontrakta_norm):
       GET https://zakup.sk.kz/#/ext?tabs=contractCard&sn=<номер>&page=1
       -> cid одного или нескольких договоров (основной + доп. соглашения).
       Без ccs портал отдаёт только договоры со статусом SIGNED (Заключен).
       Если ничего не нашлось — параллельно (asyncio.gather) повторяем
       поиск отдельным запросом на каждый из _FALLBACK_STATUSES (портал
       принимает только один ccs за раз) и объединяем найденные cid.
    2. Карточка договора (попап):
       GET https://zakup.sk.kz/#/ext(popup:item/<cid>/contractCard)?tabs=contractCard&cid=<cid>&page=1

    Поля вытаскиваются регулярками из текста попапа:
      - "Основной договор № <N> от <DD.MM.YYYY>" / "Дополнительное
        соглашение № ... от ..." -> тип + KONTR_DATA_START
      - "Срок действия договора" -> KONTR_DATA_END
      - "Статус" -> KONTR_STAT

    Как и в goszakup: приоритет у "Основной договор", иначе — самый
    свежий по дате начала. Весь список найденных договоров сохраняется
    в RAW_RESPONSE.
    """

    portal_name = "Самрук-Казына"

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
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    executable_path=self._settings.chromium_executable_path,
                )
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
            ),
            ignore_https_errors=True,
        )

    async def _goto_with_retry(self, page: Page, url: str) -> bool:
        """domcontentloaded + фиксированная пауза даёт SPA-роутеру время
        собрать компонент — как в рабочем скрапере. networkidle здесь не
        используется намеренно (см. docstring класса)."""
        timeout_ms = max(int(self._settings.request_timeout_sec * 1000), 60000)
        for attempt in range(1, _NAV_MAX_RETRIES + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(_POST_NAV_WAIT_MS)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Samruk goto attempt %d/%d failed for %s: %s", attempt, _NAV_MAX_RETRIES, url, exc)
                await page.wait_for_timeout(3000)
        return False

    @staticmethod
    def _build_search_url(sn: str, status: str | None = None) -> str:
        """Собирает URL поиска. status=None -> дефолт портала (SIGNED).
        Портал принимает только один ccs за запрос, поэтому это всегда
        одиночный статус, не список."""
        params = [("tabs", "contractCard"), ("sn", sn), ("page", "1")]
        if status:
            params.append(("ccs", status))
        return "https://zakup.sk.kz/#/ext?" + urlencode(params)

    async def _search_with_status(self, nomer_kontrakta_norm: str, status: str | None) -> list[int]:
        """Возвращает cid договоров, найденных по системному номеру,
        для ОДНОГО статуса (ccs). status=None -> дефолт портала (SIGNED)."""
        url = self._build_search_url(nomer_kontrakta_norm, status)
        context = await self._new_context()

        ids: list[int] = []
        seen: set[int] = set()
        try:
            page = await context.new_page()
            if not await self._goto_with_retry(page, url):
                raise RuntimeError(f"Не удалось загрузить страницу поиска: {url}")

            try:
                await page.wait_for_selector(_SPINNER_SELECTOR, state="hidden", timeout=5000)
            except Exception:  # noqa: BLE001 — спиннера может не быть вовсе
                pass
            # небольшая доп. пауза на отрисовку списка SPA
            await page.wait_for_timeout(1500)

            # запасной путь: карточки результатов поиска (класс из рабочего
            # скрапера), на случай если ссылка не содержит /contractCard/
            item_nums: list[str] = await page.locator(".m-found-item__num").evaluate_all(
                "els => els.map(e => e.innerText || '')"
            )
            for t in item_nums:
                mm = re.search(r"([0-9]{6,})", t)
                if not mm:
                    continue
                cid = int(mm.group(1))
                if cid not in seen:
                    seen.add(cid)
                    ids.append(cid)

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Samruk search error for nomer_kontrakta_norm=%s status=%s", nomer_kontrakta_norm, status
            )
            raise ValueError(f"Playwright error during search (status={status}): {exc}") from exc
        finally:
            await context.close()

        return ids

    async def _search(self, nomer_kontrakta_norm: str) -> list[int]:
        """Сначала ищем в дефолтном статусе портала (SIGNED). Если пусто —
        параллельно (asyncio.gather) запрашиваем каждый из
        _FALLBACK_STATUSES по отдельности (портал принимает только один
        ccs за раз) и объединяем найденные cid."""
        ids = await self._search_with_status(nomer_kontrakta_norm, status=None)
        if ids:
            return ids

        logger.info(
            "Samruk: не найдено в SIGNED, повторяю поиск параллельно по ccs=%s", _FALLBACK_STATUSES
        )
        results = await asyncio.gather(
            *[self._search_with_status(nomer_kontrakta_norm, status=s) for s in _FALLBACK_STATUSES],
            return_exceptions=True,
        )

        combined: list[int] = []
        seen: set[int] = set()
        for status, res in zip(_FALLBACK_STATUSES, results):
            if isinstance(res, Exception):
                logger.warning("Samruk fallback search failed for ccs=%s: %s", status, res)
                continue
            for cid in res:
                if cid not in seen:
                    seen.add(cid)
                    combined.append(cid)

        return combined

    async def _fetch_detail(self, cid: int) -> dict:
        """Открывает попап карточки договора и вытаскивает поля.

        Тип/№/дата начала ("Основной договор № ... от ...") и статус
        рендерятся в карточке списка результатов (классы m-found-item__*),
        которая остаётся в DOM позади попапа (Angular Material диалог
        накладывается поверх, а не заменяет страницу) — поэтому они
        читаются из текста всей страницы. Дата окончания — из блока
        попапа .m-infoblock__layout с заголовком "Срок действия
        договора", извлекается через селектор (надёжнее регекса по
        всему тексту, т.к. в разметке может быть несколько дат/блоков).

        ВАЖНО (не проверено на реальной странице): если по системному
        номеру находится несколько договоров (основной + доп.
        соглашения), под попапом в списке останутся видны ВСЕ найденные
        карточки — регекс по body_text возьмёт первое совпадение
        "Статус", что не обязательно относится к текущему cid. Если
        статус будет "перескакивать" между договорами одного номера —
        нужно сузить поиск до конкретной .m-found-item карточки с
        этим cid, а не читать весь body.
        """
        url = self._DETAIL_URL_TMPL.format(cid=cid)
        context = await self._new_context()
        try:
            page = await context.new_page()
            if not await self._goto_with_retry(page, url):
                raise RuntimeError(f"Не удалось загрузить карточку договора: {url}")

            try:
                await page.locator(_POPUP_SELECTOR).first.wait_for(state="visible", timeout=15000)
            except Exception:  # noqa: BLE001 — попап не поймали отдельным селектором, читаем что есть
                logger.warning("Samruk popup selector not matched for cid=%s", cid)

            # count()+loop берёт снимок DOM в один момент и не ждёт —
            # содержимое попапа может дозагрузиться уже ПОСЛЕ того, как
            # сам диалог стал visible (Angular тянет данные вкладки
            # отдельным запросом). Поэтому здесь — locator с has_text и
            # явным wait_for, который сам ретраит до таймаута.
            kontr_data_end: datetime | None = None
            end_title_loc = page.locator(".m-infoblock__title", has_text="Срок действия договора")
            try:
                await end_title_loc.first.wait_for(state="visible", timeout=15000)
                title_text = await end_title_loc.first.inner_text()
                block = end_title_loc.first.locator("xpath=..")  # родитель .m-infoblock__layout
                full_text = await block.inner_text()
                value_text = full_text.replace(title_text, "", 1).strip()
                kontr_data_end = _parse_dmy(value_text)
            except Exception:  # noqa: BLE001 — блок не появился за таймаут, уйдём в запасной regex ниже
                logger.warning("Samruk .m-infoblock__title (срок действия) not found for cid=%s", cid)

            # читаем body ПОСЛЕ ожидания инфоблока — тем самым title/status
            # (регексом ниже) тоже получают дополнительное время на отрисовку,
            # а не читаются раньше, чем поле, которое как раз не находилось
            body_text = await page.locator("body").inner_text()
        finally:
            await context.close()

        title_match = _TITLE_RE.search(body_text)
        status_match = _STATUS_RE.search(body_text)
        if kontr_data_end is None:
            # запасной путь, если .m-infoblock__layout не нашёлся / сменилась вёрстка
            end_match = _END_DATE_RE.search(body_text)
            kontr_data_end = _parse_dmy(end_match.group(1)) if end_match else None

        return {
            "id": cid,
            "url": url,
            "type": title_match.group("type").strip() if title_match else None,
            "kontr_data_start": _parse_dmy(title_match.group("date")) if title_match else None,
            "kontr_data_end": kontr_data_end,
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
                    parse_source_url=self._build_search_url(search_term),
                    error_message=(
                        "Контракт не найден на zakup.sk.kz по системному номеру "
                        "(проверены SIGNED и остальные статусы)"
                    ),
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
                parse_source_url=self._DETAIL_URL_TMPL.format(cid=ids[0]) if ids else self._build_search_url(search_term),
                error_message=f"Playwright error: {exc}",
            )