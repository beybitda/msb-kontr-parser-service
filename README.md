# msb_kontr_parser_service

Сервис по архитектуре из `MSB_KONTR_PARSER_ARCHITECTURE.md`:
Informatica (post-session command) → HTTP-триггер → фоновый пайплайн
`GAP_ANALYSIS → PARSE_GOSZAKUP → PARSE_SAMRUK → UPDATE_TARGET_TABLE`,
мониторинг в общей таблице `MSB_DB_PROCESS_MONITOR`. Airflow не используется.

## Статус этой поставки

**Реализован весь сервис, кроме самого парсинга сайтов.**

Работает:
- FastAPI-приложение, пул соединений Oracle (`oracledb`, thin-режим).
- `POST /parser/trigger` — приём вызова от Informatica, идемпотентность
  по `process_run_id` (ALREADY_RUNNING / ALREADY_SUCCESS / ACCEPTED),
  проверка `X-API-Key`.
- `GET /parser/status/{process_run_id}` — статус всех 4 шагов сервиса.
- `GET /health`.
- `GAP_ANALYSIS`: выборка контрактов без `KONTR_STAT` из
  `MSB_DB_GRN_BLANK_MONITOR`, `MERGE` в очередь `MSB_DB_KONTR_PARSE`
  (новые → `NEW`, незавершённые с запасом попыток → на ретрай).
- Оркестрация всех 4 шагов с записью строки в `MSB_DB_PROCESS_MONITOR`
  на каждый шаг (`RUNNING` → `SUCCESS`/`FAILED`), паузы между запросами,
  учёт `ATTEMPT_NUMBER` / `MAX_ATTEMPTS` → `NOT_FOUND`.
- `UPDATE_TARGET_TABLE`: `MERGE` из стейджинга в целевую витрину.
- Нормализация `NOMER_KONTRAKTA` (пробелы, дефисы/тире, ведущие нули).
- Тесты (`pytest`, 9 шт.) на нормализатор и на пайплайн оркестратора
  с замоканной БД.
- DDL для `MSB_DB_KONTR_PARSE` и `MSB_DB_PROCESS_MONITOR` (`sql/ddl.sql`).

**Не реализовано (заглушки):**
- `app/parsers/goszakup_parser.py` и `app/parsers/samruk_parser.py` —
  интерфейс `ParserAdapter.parse()` подключён к пайплайну, но возвращает
  `ParseResult(status_name=ERROR, error_message="... not implemented yet")`.
  Реальный HTTP/HTML-парсинг сайтов goszakup.gov.kz и zakup.sk.kz нужно
  дописать в этих двух файлах — остальной пайплайн (очередь, ретраи,
  мониторинг, merge в витрину) уже готов и не потребует изменений.

## Запуск

```bash
cp .env.example .env   # заполнить DB_USER/DB_PASSWORD/DB_DSN и PARSER_API_KEY
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Или через Docker:

```bash
docker build -t msb-kontr-parser .
docker run --env-file .env -p 8001:8001 msb-kontr-parser
```

Перед первым запуском накатить `sql/ddl.sql` в схему `ANALYST_MSB2`
(если таблицы ещё не созданы; `MSB_DB_PROCESS_MONITOR` может уже
существовать в контуре — тогда просто сверить набор колонок).

## Вызов из Informatica

Post-session success command (пример):

```bash
curl -s -X POST http://<service-host>:8001/parser/trigger \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $PARSER_API_KEY" \
  -d "{\"business_date\": \"$BusinessDate\"}"
```

## Тесты

```bash
pip install -r requirements.txt pytest
pytest -q
```

## Дальнейшие шаги

1. Реализовать `GoszakupParser.parse()` и `SamrukParser.parse()`:
   сначала проверить в DevTools → Network, есть ли JSON API у обоих
   порталов (см. заметки в архитектурном документе), иначе —
   Playwright/Selenium.
2. Накатить `sql/ddl.sql`, свериться с реальной структурой
   `MSB_DB_PROCESS_MONITOR`, если она уже существует в контуре.
3. Настроить post-session command в Informatica-workflow.
4. Прогнать e2e на тестовом наборе контрактов с известными «дырками».
