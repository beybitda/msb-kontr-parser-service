import asyncio

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


def test_goszakup_parser_stub_returns_error() -> None:
    row = GapRow(kontr_id=1, nomer_kontrakta="123-45", nomer_kontrakta_norm="123-45", naim_portala="Госзакупки", ord_id=1)
    result = asyncio.run(GoszakupParser().parse(row))
    assert result.status_name == StatusName.ERROR
    assert result.kontr_id == 1


def test_samruk_parser_stub_returns_error() -> None:
    row = GapRow(kontr_id=2, nomer_kontrakta="123-45", nomer_kontrakta_norm="123-45", naim_portala="Самрук-Казына", ord_id=2)
    result = asyncio.run(SamrukParser().parse(row))
    assert result.status_name == StatusName.ERROR
    assert result.kontr_id == 2
