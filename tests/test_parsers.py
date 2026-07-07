import asyncio
from datetime import date

import pytest

from app.models.dto import GapRow, StatusName
from app.parsers.goszakup_parser import GoszakupParser
from app.parsers.normalizer import normalize_nomer
from app.parsers.samruk_parser import SamrukParser


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  123 - 045  ", "123-45"),
        ("123—045", "123-45"),
        ("123–045", "123-45"),
        ("ABC-007-2024", "ABC-7-2024"),
        ("000", "0"),
        ("", ""),
    ],
)
def test_normalize_nomer(raw: str, expected: str) -> None:
    assert normalize_nomer(raw) == expected


def test_samruk_parser_stub_returns_error() -> None:
    row = GapRow(kontr_id=2, nomer_kontrakta="123-45", nomer_kontrakta_norm="123-45", naim_portala="Самрук-Казына", ord_id=2, dep_id=None)
    result = asyncio.run(SamrukParser().parse(row))
    assert result.status_name == StatusName.ERROR
    assert result.kontr_id == 2


# --- GoszakupParser: юнит-тесты мокают _search/_fetch_detail, чтобы не
# ходить в реальную сеть goszakup.gov.kz ---


def test_goszakup_parser_not_found_when_search_has_no_results(monkeypatch) -> None:
    parser = GoszakupParser()

    async def fake_search(nomer: str) -> list[int]:
        return []

    monkeypatch.setattr(parser, "_search", fake_search)

    row = GapRow(kontr_id=2, nomer_kontrakta="130140023286/250002/00", nomer_kontrakta_norm="130140023286/250002/00", naim_portala="Гос. Закупки", ord_id=3537408323, dep_id=10306)
    result = asyncio.run(parser.parse(row))

    assert result.status_name == StatusName.NOT_FOUND
    assert result.kontr_id == 2


def test_goszakup_parser_done_with_single_main_contract(monkeypatch) -> None:
    parser = GoszakupParser()

    async def fake_search(nomer: str) -> list[int]:
        assert nomer == "001140002478/260030/00"
        return [24694320]

    async def fake_fetch_detail(contract_id: int) -> dict:
        return {
            "id": contract_id,
            "url": f"https://goszakup.gov.kz/ru/egzcontract/cpublic/show/{contract_id}",
            "type": "Основной договор",
            "kontr_data_start": date(2026, 2, 20),
            "kontr_data_end": date(2026, 12, 31),
            "kontr_stat": "Действует",
        }

    monkeypatch.setattr(parser, "_search", fake_search)
    monkeypatch.setattr(parser, "_fetch_detail", fake_fetch_detail)

    row = GapRow(
        kontr_id=1,
        nomer_kontrakta="001140002478/260030/00",
        nomer_kontrakta_norm="001140002478/260030/00",
        naim_portala="Госзакупки",
        ord_id=1,
        dep_id=None,
    )
    result = asyncio.run(parser.parse(row))

    assert result.status_name == StatusName.DONE
    assert result.kontr_data_start == date(2026, 2, 20)
    assert result.kontr_data_end == date(2026, 12, 31)
    assert result.kontr_stat == "Действует"
    assert "24694320" in result.raw_response


def test_goszakup_parser_picks_main_contract_among_several_matches(monkeypatch) -> None:
    """Если по одному системному номеру найдено несколько договоров
    (основной + доп. соглашение), KONTR_STAT/даты берутся с основного,
    а весь список сохраняется в raw_response."""
    parser = GoszakupParser()

    async def fake_search(nomer: str) -> list[int]:
        return [111, 222]

    async def fake_fetch_detail(contract_id: int) -> dict:
        if contract_id == 111:
            return {
                "id": 111,
                "url": "https://goszakup.gov.kz/ru/egzcontract/cpublic/show/111",
                "type": "Основной договор",
                "kontr_data_start": date(2026, 1, 1),
                "kontr_data_end": date(2026, 12, 31),
                "kontr_stat": "Изменен",
            }
        return {
            "id": 222,
            "url": "https://goszakup.gov.kz/ru/egzcontract/cpublic/show/222",
            "type": "Дополнительное соглашение",
            "kontr_data_start": date(2026, 6, 1),
            "kontr_data_end": date(2026, 12, 31),
            "kontr_stat": "Создано доп.соглашение",
        }

    monkeypatch.setattr(parser, "_search", fake_search)
    monkeypatch.setattr(parser, "_fetch_detail", fake_fetch_detail)

    row = GapRow(kontr_id=1, nomer_kontrakta="1", nomer_kontrakta_norm="1", naim_portala="Госзакупки", ord_id=1, dep_id=None)
    result = asyncio.run(parser.parse(row))

    assert result.status_name == StatusName.DONE
    assert result.kontr_stat == "Изменен"
    assert result.kontr_data_start == date(2026, 1, 1)
    assert "222" in result.raw_response  # доп. соглашение тоже сохранено