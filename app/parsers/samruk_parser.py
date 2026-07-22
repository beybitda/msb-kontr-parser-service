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
from app.parsers.normalizer import normalize_nomer

logger = logging.getLogger(__name__)

# Попап карточки договора: #/ext(popup:item/5453559047/contractCard)?tabs=contractCard&cid=5453559047&page=1
_CID_RE = re.compile(r"item/(\d+)/contractCard")

# Заголовок карточки в списке результатов поиска, например:
# "Основной договор № 1076911/2025/3 от 19.05.2025"
_TITLE_RE = re.compile(
    r"(?P<type>[^\n№]+?)\s*№\s*(?P<num>\S+)\s*от\s*(?P<date>\d{2}\.\d{2}\.\d{4})"
)

_MAIN_CONTRACT_TYPE = "Основной договор"

# Портал матчит поиск по ПРЕФИКСУ номера (например "1076911/2025/5"
# находит все "1076911/2025/*"), поэтому по одному запросу может
# вернуться несколько РАЗНЫХ договоров с общим префиксом — точное
# совпадение номера проверяется отдельно по заголовку каждой карточки
# (_TITLE_RE -> num), а не по факту наличия в выдаче.
#
# Портал принимает только ОДИН ccs (статус) за запрос — поэтому все
# статусы запрашиваются параллельно (asyncio.gather), а не
# последовательно с fallback'ом, как было раньше: последовательный
# fallback (сначала SIGNED, потом остальные только если SIGNED пуст)
# давал неполный набор договоров по номеру, если, например, основной
# договор был в статусе EXECUTED, а SIGNED-запрос уже что-то нашёл (с
# другим точным номером под тем же префиксом) и fallback не запускался.
_ALL_STATUSES = [
    "SIGNED",
    "RESCIND",
    "EXECUTED",
    "SUPPLEMENTARY_AGREEMENT",
    "REFUSAL_PERFORM_CONTRACT",
]

# Селекторы карточки в списке результатов поиска (используются для
# фильтрации по точному номеру ДО открытия попапа/детальной карточки).
_FOUND_ITEM_SELECTOR = ".m-found-item"
_FOUND_ITEM_TITLE_SELECTOR = ".m-found-item__title"
# ВНИМАНИЕ (не проверено на реальной странице): предполагается, что
# .m-found-item__num содержит внутренний числовой id договора (тот же,
# что подставляется в URL попапа item/<id>/contractCard), а НЕ номер
# контракта из ТЗ (1076911/2025/3) — это поведение унаследовано из
# предыдущей версии парсера и не менялось. Если вёрстка окажется другой,
# скорректировать здесь.
_FOUND_ITEM_NUM_SELECTOR = ".m-found-item__num"

# Селектор попапа карточки — из рабочего скрапера (scraper_card.py):
# Angular Material диалог / модалка / кастомный компонент карточки.
_POPUP_SELECTOR = ".mat-dialog-container, .modal-content, app-item-details"
_SPINNER_SELECTOR = ".mat-progress-spinner, .loading-indicator"

# Бейдж статуса внутри попапа карточки, напр.:
# <div class="m-status m-status--warning">Отказ от исполнения договора</div>
# Читается напрямую из разметки — это фактическое место рендера статуса,
# в отличие от regex по метке "Статус:" в тексте всей страницы (который
# может относиться к другой карточке в списке результатов позади попапа).
_STATUS_BADGE_SELECTOR = ".m-status"

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

    Алгоритм (обновлён — портал матчит по префиксу номера, поэтому точное
    совпадение проверяется на этапе поиска, а не после):

    1. Поиск по системному номеру (row.nomer_kontrakta_norm), ПАРАЛЛЕЛЬНО
       по всем статусам сразу (портал принимает только один ccs за
       запрос — см. _ALL_STATUSES):
       GET https://zakup.sk.kz/#/ext?tabs=contractCard&sn=<номер>&ccs=<статус>&page=1
    2. Для каждой карточки в выдаче читаем заголовок
       (.m-found-item__title, например "Основной договор № 1076911/2025/3
       от 19.05.2025") и парсим его _TITLE_RE -> type/num/дата начала.
       Карточка попадает в кандидаты, ТОЛЬКО если normalize_nomer(num)
       точно совпадает с искомым номером — иначе это чужой договор с тем
       же префиксом, отбрасываем.
    3. Среди кандидатов (по всем статусам сразу, объединены и
       дедуплицированы по cid) выбирается «основной» договор
       (_pick_primary: приоритет "Основной договор", иначе самый свежий
       по дате начала) — это единственный id, с которым работаем дальше.
    4. Детальная карточка (попап) открывается ТОЛЬКО за сроком окончания
       и статусом (тип/номер/дата начала уже известны из шага 2, повторно
       не парсятся):
       GET https://zakup.sk.kz/#/ext(popup:item/<cid>/contractCard)?tabs=contractCard&cid=<cid>&page=1
       Оба поля читаются точечно по селекторам ВНУТРИ попапа
       (.m-infoblock__title "Срок действия договора" / _STATUS_BADGE_SELECTOR),
       без regex по всему тексту попапа/страницы — под попапом в DOM
       остаётся видимым список результатов поиска, и чтение всего body
       могло бы задеть данные другого договора с тем же префиксом номера.

    Если попап не удалось открыть/распарсить — это ERROR (сбой скрапинга),
    а не NOT_FOUND: наличие подходящего договора уже подтверждено поиском
    на шаге 2-3.
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
        """Собирает URL поиска. status=None -> без ccs (используется только
        для сообщений об ошибке/URL в NOT_FOUND, не для реального поиска —
        реальный поиск всегда идёт по конкретному статусу из _ALL_STATUSES)."""
        params = [("tabs", "contractCard"), ("sn", sn), ("page", "1")]
        if status:
            params.append(("ccs", status))
        return "https://zakup.sk.kz/#/ext?" + urlencode(params)

    async def _search_with_status(
        self, nomer_kontrakta_norm: str, status: str, target_norm: str
    ) -> list[dict]:
        """Ищет договоры по системному номеру для ОДНОГО статуса (ccs) и
        сразу отфильтровывает по ТОЧНОМУ совпадению номера в заголовке
        карточки (.m-found-item__title, см. _TITLE_RE) — поиск на портале
        матчит по префиксу, поэтому без этой фильтрации в выдачу
        подмешиваются договоры с тем же префиксом, но другим номером."""
        url = self._build_search_url(nomer_kontrakta_norm, status)
        context = await self._new_context()

        matched: list[dict] = []
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

            items = page.locator(_FOUND_ITEM_SELECTOR)
            count = await items.count()

            for i in range(count):
                item = items.nth(i)
                try:
                    # ВНИМАНИЕ: внутри одной .m-found-item бывает НЕСКОЛЬКО
                    # .m-found-item__title — первый это предмет закупки
                    # ("Текущий ремонт резервуара..."), и только один из
                    # остальных — нужный "Основной договор № ... от ..."
                    # (или "Дополнительное соглашение № ... от ..."). Поэтому
                    # перебираем все заголовки карточки и ищем тот, что
                    # матчится _TITLE_RE, а не берём первый попавшийся.
                    title_texts = await item.locator(_FOUND_ITEM_TITLE_SELECTOR).all_inner_texts()
                    num_text = await item.locator(_FOUND_ITEM_NUM_SELECTOR).first.inner_text()
                except Exception:  # noqa: BLE001 — карточка без ожидаемой разметки, пропускаем
                    continue

                title_match = None
                for title_text in title_texts:
                    m = _TITLE_RE.search(title_text)
                    if m:
                        title_match = m
                        break
                if title_match is None:
                    continue

                num = title_match.group("num").strip()
                if normalize_nomer(num) != target_norm:
                    continue  # чужой договор с тем же префиксом номера

                cid_match = re.search(r"([0-9]{6,})", num_text)
                if not cid_match:
                    logger.warning(
                        "Samruk: карточка с совпавшим номером %s без id (status=%s)", num, status
                    )
                    continue

                matched.append(
                    {
                        "id": int(cid_match.group(1)),
                        "type": title_match.group("type").strip(),
                        "num": num,
                        "kontr_data_start": _parse_dmy(title_match.group("date")),
                    }
                )

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Samruk search error for nomer_kontrakta_norm=%s status=%s", nomer_kontrakta_norm, status
            )
            raise ValueError(f"Playwright error during search (status={status}): {exc}") from exc
        finally:
            await context.close()

        return matched

    async def _search(self, nomer_kontrakta_norm: str) -> dict | None:
        """Параллельно (asyncio.gather) запрашивает ВСЕ статусы разом
        (_ALL_STATUSES — портал принимает только один ccs за запрос),
        объединяет и дедуплицирует по id уже отфильтрованные (по точному
        номеру) кандидаты и выбирает среди них «основной» договор
        (_pick_primary). Возвращает единственный словарь-кандидат
        (id + type/num/kontr_data_start) или None, если точных совпадений
        не найдено ни в одном статусе."""
        target_norm = normalize_nomer(nomer_kontrakta_norm)

        results = await asyncio.gather(
            *[
                self._search_with_status(nomer_kontrakta_norm, status, target_norm)
                for status in _ALL_STATUSES
            ],
            return_exceptions=True,
        )

        combined: list[dict] = []
        seen: set[int] = set()
        for status, res in zip(_ALL_STATUSES, results):
            if isinstance(res, Exception):
                logger.warning("Samruk search failed for ccs=%s: %s", status, res)
                continue
            for item in res:
                if item["id"] not in seen:
                    seen.add(item["id"])
                    combined.append(item)

        if not combined:
            return None

        return self._pick_primary(combined)

    async def _fetch_detail(self, cid: int) -> dict:
        """Открывает попап карточки договора и вытаскивает ТОЛЬКО срок
        окончания и статус — тип/номер/дата начала уже известны из
        _search_with_status (заголовок в списке результатов) и здесь не
        перечитываются. Оба поля читаются строго из элементов ВНУТРИ
        попапа (_POPUP_SELECTOR), а не всей страницы: под попапом в DOM
        остаётся видимым список результатов поиска, и чтение всего body
        может зацепить данные другого договора с тем же префиксом номера.

        Если попап не появился за таймаут — это сбой скрапинга (ERROR),
        поэтому исключение не глушится, а поднимается наверх."""
        url = self._DETAIL_URL_TMPL.format(cid=cid)
        context = await self._new_context()
        try:
            page = await context.new_page()
            if not await self._goto_with_retry(page, url):
                raise RuntimeError(f"Не удалось загрузить карточку договора: {url}")

            popup = page.locator(_POPUP_SELECTOR).first
            await popup.wait_for(state="visible", timeout=15000)

            kontr_data_end: datetime | None = None
            end_title_loc = popup.locator(".m-infoblock__title", has_text="Срок действия договора")
            try:
                await end_title_loc.first.wait_for(state="visible", timeout=15000)
                title_text = await end_title_loc.first.inner_text()
                block = end_title_loc.first.locator("xpath=..")  # родитель .m-infoblock__layout
                full_text = await block.inner_text()
                value_text = full_text.replace(title_text, "", 1).strip()
                kontr_data_end = _parse_dmy(value_text)
            except Exception:  # noqa: BLE001
                logger.warning("Samruk .m-infoblock__title (срок действия) not found in popup for cid=%s", cid)

            kontr_stat: str | None = None
            status_loc = popup.locator(_STATUS_BADGE_SELECTOR)
            try:
                await status_loc.first.wait_for(state="visible", timeout=15000)
                kontr_stat = (await status_loc.first.inner_text()).strip()
            except Exception:  # noqa: BLE001
                logger.warning("Samruk %s (статус) not found in popup for cid=%s", _STATUS_BADGE_SELECTOR, cid)

        finally:
            await context.close()

        return {
            "id": cid,
            "url": url,
            "kontr_data_end": kontr_data_end,
            "kontr_stat": kontr_stat,
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
            if "kontr_data_start" in item:
                item["kontr_data_start"] = (
                    d["kontr_data_start"].isoformat() if d.get("kontr_data_start") else None
                )
            if "kontr_data_end" in item:
                item["kontr_data_end"] = d["kontr_data_end"].isoformat() if d.get("kontr_data_end") else None
            serializable.append(item)
        return json.dumps(serializable, ensure_ascii=False)

    async def parse(self, row: GapRow) -> ParseResult:
        search_term = row.nomer_kontrakta_norm or row.nomer_kontrakta
        primary_id: int | None = None
        try:
            primary = await self._search(search_term)
            if primary is None:
                return ParseResult(
                    kontr_id=row.kontr_id,
                    status_name=StatusName.NOT_FOUND,
                    parse_source_url=self._build_search_url(search_term),
                    error_message=(
                        "Контракт не найден на zakup.sk.kz по системному номеру "
                        "(проверены все статусы, точных совпадений номера нет)"
                    ),
                )

            primary_id = primary["id"]
            detail = await self._fetch_detail(primary_id)

            return ParseResult(
                kontr_id=row.kontr_id,
                status_name=StatusName.DONE,
                kontr_data_start=primary["kontr_data_start"],
                kontr_data_end=detail["kontr_data_end"],
                kontr_stat=detail["kontr_stat"],
                parse_source_url=detail["url"],
                raw_response=self._serialize_details([{**primary, **detail}]),
            )
        except Exception as exc:  # noqa: BLE001 — Playwright: timeout/navigation/etc.
            logger.exception("Samruk parse error for kontr_id=%s", row.kontr_id)
            return ParseResult(
                kontr_id=row.kontr_id,
                status_name=StatusName.ERROR,
                parse_source_url=(
                    self._DETAIL_URL_TMPL.format(cid=primary_id) if primary_id else self._build_search_url(search_term)
                ),
                error_message=f"Playwright error: {exc}",
            )