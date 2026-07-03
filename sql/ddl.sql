-- =====================================================================
-- MSB_DB_KONTR_PARSE — очередь + результат парсинга (из архитектурного документа)
-- =====================================================================

CREATE TABLE ANALYST_MSB2.MSB_DB_KONTR_PARSE (
    KONTR_ID              NUMBER          NOT NULL,
    NOMER_KONTRAKTA        VARCHAR2(500)   NOT NULL,
    NOMER_KONTRAKTA_NORM   VARCHAR2(500),                  -- нормализованный номер для поиска на сайте
    NAIM_PORTALA           VARCHAR2(500)   NOT NULL,
    ORD_ID                 NUMBER(10,0),                   -- ссылка на сделку из MSB_DB_GRN_BLANK_MONITOR.ORD_ID
    DEP_ID                 NUMBER(10,0),                   -- подразделение сделки из MSB_DB_GRN_BLANK_MONITOR.DEP_ID
    -- очередь / статус обработки
    STATUS_NAME            VARCHAR2(20) DEFAULT 'NEW',     -- NEW, IN_PROGRESS, DONE, NOT_FOUND, ERROR
    ATTEMPT_NUMBER          NUMBER      DEFAULT 0,
    LAST_ATTEMPT_DATE       TIMESTAMP(6),
    LAST_ERROR              VARCHAR2(4000),
    -- результат парсинга
    KONTR_DATA_START        DATE,
    KONTR_DATA_END          DATE,
    KONTR_STAT              VARCHAR2(150),
    PARSE_SOURCE_URL         VARCHAR2(1000),                -- откуда спарсили, для аудита
    RAW_RESPONSE             CLOB,                           -- сырой JSON/HTML ответ (для отладки)
    PROCESS_RUN_ID            VARCHAR2(500),                  -- run_id сервиса, обработавшего запись
    INSERTED_AT                TIMESTAMP(6) DEFAULT SYSTIMESTAMP,
    UPDATED_AT                  TIMESTAMP(6) DEFAULT SYSTIMESTAMP,
    CONSTRAINT PK_KONTR_PARSE PRIMARY KEY (KONTR_ID)
);

CREATE SEQUENCE ANALYST_MSB2.SEQ_KONTR_PARSE START WITH 1 INCREMENT BY 1;

CREATE INDEX ANALYST_MSB2.IX_KP_NOMER  ON ANALYST_MSB2.MSB_DB_KONTR_PARSE (NOMER_KONTRAKTA);
CREATE INDEX ANALYST_MSB2.IX_KP_STATUS ON ANALYST_MSB2.MSB_DB_KONTR_PARSE (STATUS_NAME);
CREATE INDEX ANALYST_MSB2.IX_KP_RUN    ON ANALYST_MSB2.MSB_DB_KONTR_PARSE (PROCESS_RUN_ID);