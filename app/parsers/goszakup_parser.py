from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.models.dto import GapRow, ParseResult, StatusName
from app.parsers.base import ParserAdapter

logger = logging.getLogger(__name__)

# Ссылки на карточку договора в реестре имеют вид
# https://goszakup.gov.kz/ru/egzcontract/cpublic/show/24694320
_SHOW_ID_RE = re.compile(r"/ru/egzcontract/cpublic/show/(\d+)")

_TYPE_LABEL = "Тип"
_START_LABEL = "Дата создания договора"
_END_LABEL = "Срок действия договора"
_STATUS_LABEL = "Статус договора"
_MAIN_CONTRACT_TYPE = "Основной договор"


def _parse_ru_date(value: str | None) -> date | None:
   """'2026-02-20 11:49:39' / '2026-12-31' -> date, иначе None."""
   value = (value or "").strip()
   if not value:
      return None
   for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
      try:
         return datetime.strptime(value, fmt).date()
      except ValueError:
         continue
   logger.warning("Не удалось распарсить дату goszakup: %r", value)
   return None


class GoszakupParser(ParserAdapter):
   """Адаптер под https://goszakup.gov.kz/ru/registry/contract

   Официальный API (OWS v3, GraphQL/REST на ows.goszakup.gov.kz) требует
   Bearer-токен, который выдаётся по заявке в АО «Центр электронных
   финансов» (support@ecc.kz) — токена пока нет, поэтому используется
   публичный HTML-интерфейс портала, без браузера/JS:

   1. Поиск ID договора(ов) по системному номеру (NOMER_KONTRAKTA_NORM):
      GET https://goszakup.gov.kz/ru/registry/contract
         ?filter[number]=<номер>&count_record=50&page=1
      На один системный номер больше 50 договоров не встречается, поэтому
      одной страницы достаточно — доп. пагинация не нужна.
   2. Для каждого найденного ID — детальная карточка (обычный
      server-rendered HTML, без JS):
      GET https://goszakup.gov.kz/ru/egzcontract/cpublic/show/<id>
      Таблица «Общие сведения» содержит:
      Тип                      -> "Основной договор" / "Дополнительное соглашение"
      Дата создания договора   -> KONTR_DATA_START
      Срок действия договора   -> KONTR_DATA_END
      Статус договора          -> KONTR_STAT

   Если по номеру находится несколько договоров (основной + доп.
   соглашения с тем же системным номером), то для колонок
   KONTR_STAT/KONTR_DATA_START/KONTR_DATA_END берётся «главная» запись:
   приоритет — Тип = "Основной договор", иначе самая свежая по «Дата
   создания договора». Весь список найденных договоров целиком
   сохраняется в RAW_RESPONSE (JSON) для аудита/ручного разбора.
   """

   portal_name = "Госзакупки"

   def __init__(self) -> None:
      settings = get_settings()
      self._settings = settings
      self._client = httpx.AsyncClient(
         timeout=settings.request_timeout_sec,
         follow_redirects=True,
         headers={
               "User-Agent": (
                  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
               )
         },
      )

   async def aclose(self) -> None:
      await self._client.aclose()

   async def _search(self, nomer_kontrakta_norm: str) -> list[int]:
      """Возвращает внутренние ID договоров, найденных по номеру
      (в порядке появления на странице)."""
      resp = await self._client.get(
         self._settings.goszakup_base_url,
         params={
               "filter[number]": nomer_kontrakta_norm,
               "count_record": self._settings.goszakup_search_count_record,
               "page": 1,
         },
      )
      resp.raise_for_status()
      soup = BeautifulSoup(resp.text, "html.parser")

      ids: list[int] = []
      seen: set[int] = set()
      for a in soup.find_all("a", href=_SHOW_ID_RE):
         match = _SHOW_ID_RE.search(a["href"])
         if not match:
               continue
         contract_id = int(match.group(1))
         if contract_id in seen:
               continue
         seen.add(contract_id)
         ids.append(contract_id)
      return ids

   async def _fetch_detail(self, contract_id: int) -> dict:
      """Парсит таблицу «Общие сведения» детальной карточки договора."""
      url = self._settings.goszakup_detail_url_template.format(id=contract_id)
      resp = await self._client.get(url)
      resp.raise_for_status()
      soup = BeautifulSoup(resp.text, "html.parser")

      fields: dict[str, str] = {}
      for row in soup.find_all("tr"):
         cells = row.find_all(["td", "th"])
         if len(cells) < 2:
               continue
         label = cells[0].get_text(strip=True)
         value = cells[1].get_text(strip=True)
         if label and label not in fields:
               fields[label] = value

      return {
         "id": contract_id,
         "url": url,
         "type": fields.get(_TYPE_LABEL),
         "kontr_data_start": _parse_ru_date(fields.get(_START_LABEL)),
         "kontr_data_end": _parse_ru_date(fields.get(_END_LABEL)),
         "kontr_stat": fields.get(_STATUS_LABEL) or None,
      }

   @staticmethod
   def _pick_primary(details: list[dict]) -> dict:
      main = next((d for d in details if d["type"] == _MAIN_CONTRACT_TYPE), None)
      if main is not None:
         return main
      return max(details, key=lambda d: d["kontr_data_start"] or date.min)

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
      try:
         ids = await self._search(search_term)
         if not ids:
               return ParseResult(
                  kontr_id=row.kontr_id,
                  status_name=StatusName.NOT_FOUND,
                  parse_source_url=self._settings.goszakup_base_url,
                  error_message="Контракт не найден на goszakup.gov.kz по системному номеру",
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
      except httpx.HTTPError as exc:
         logger.exception("Goszakup HTTP error for kontr_id=%s", row.kontr_id)
         return ParseResult(
               kontr_id=row.kontr_id,
               status_name=StatusName.ERROR,
               parse_source_url=self._settings.goszakup_base_url,
               error_message=f"HTTP error: {exc}",
         )