from __future__ import annotations

import logging

from app.db.connection import get_connection
from app.models.dto import GapRow
from app.parsers.normalizer import normalize_nomer

logger = logging.getLogger(__name__)

# Источник дырок: контракты витрины без статуса, с непустым номером и
# известным порталом-источником.
_SELECT_SOURCE_GAPS_SQL = """
SELECT ORD_ID, NOMER_KONTRAKTA, NAIM_PORTALA
FROM ANALYST_MSB2.MSB_DB_GRN_BLANK_MONITOR
WHERE KONTR_STAT IS NULL
  AND NOMER_KONTRAKTA IS NOT NULL
"""

# MERGE в очередь: новые дырки -> NEW; существующие незавершённые
# (STATUS_NAME != DONE) с запасом попыток -> переиспользуются для ретрая.
_MERGE_QUEUE_SQL = """
MERGE INTO ANALYST_MSB2.MSB_DB_KONTR_PARSE t
USING (SELECT :ord_id AS ORD_ID, :nomer AS NOMER_KONTRAKTA, :nomer_norm AS NOMER_KONTRAKTA_NORM,
              :portal AS NAIM_PORTALA FROM DUAL) src
ON (t.NOMER_KONTRAKTA = src.NOMER_KONTRAKTA)
WHEN NOT MATCHED THEN
    INSERT (KONTR_ID, NOMER_KONTRAKTA, NOMER_KONTRAKTA_NORM, NAIM_PORTALA, ORD_ID,
            STATUS_NAME, ATTEMPT_NUMBER, PROCESS_RUN_ID, INSERTED_AT, UPDATED_AT)
    VALUES (ANALYST_MSB2.SEQ_KONTR_PARSE.NEXTVAL, src.NOMER_KONTRAKTA, src.NOMER_KONTRAKTA_NORM,
            src.NAIM_PORTALA, src.ORD_ID, 'NEW', 0, :process_run_id, SYSTIMESTAMP, SYSTIMESTAMP)
WHEN MATCHED THEN UPDATE SET
    t.PROCESS_RUN_ID = :process_run_id,
    t.UPDATED_AT = SYSTIMESTAMP
    WHERE t.STATUS_NAME != 'DONE' AND t.ATTEMPT_NUMBER < :max_attempts
"""

_SELECT_QUEUE_FOR_RUN_SQL = """
SELECT KONTR_ID, NOMER_KONTRAKTA, NOMER_KONTRAKTA_NORM, NAIM_PORTALA, ORD_ID, ATTEMPT_NUMBER
FROM ANALYST_MSB2.MSB_DB_KONTR_PARSE
WHERE PROCESS_RUN_ID = :process_run_id
  AND STATUS_NAME IN ('NEW', 'IN_PROGRESS', 'ERROR')
  AND NAIM_PORTALA LIKE '%' || :portal || '%'
"""


def fetch_gaps(process_run_id: str, max_attempts: int) -> list[GapRow]:
    """GAP_ANALYSIS: находит контракты без статуса в целевой витрине,
    MERGE-ит их в очередь MSB_DB_KONTR_PARSE и возвращает актуальный
    набор строк, которые нужно спарсить в рамках текущего process_run_id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SELECT_SOURCE_GAPS_SQL)
        source_rows = cur.fetchall()

        for ord_id, nomer, portal in source_rows:
            cur.execute(
                _MERGE_QUEUE_SQL,
                {
                    "ord_id": ord_id,
                    "nomer": nomer,
                    "nomer_norm": normalize_nomer(nomer),
                    "portal": portal,
                    "process_run_id": process_run_id,
                    "max_attempts": max_attempts,
                },
            )

        result: list[GapRow] = []
        for portal in ("Гос", "Самрук"):
            cur.execute(_SELECT_QUEUE_FOR_RUN_SQL, {"process_run_id": process_run_id, "portal": portal})
            for kontr_id, nomer, nomer_norm, naim_portala, ord_id, attempt in cur.fetchall():
                result.append(
                    GapRow(
                        kontr_id=kontr_id,
                        nomer_kontrakta=nomer,
                        nomer_kontrakta_norm=nomer_norm,
                        naim_portala=naim_portala,
                        ord_id=ord_id,
                        attempt_number=attempt,
                    )
                )

        logger.info("GAP_ANALYSIS: source=%d queued_for_run=%d", len(source_rows), len(result))
        return result
