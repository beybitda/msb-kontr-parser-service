from __future__ import annotations

import re

_MULTISPACE_RE = re.compile(r"\s+")


def normalize_nomer(nomer_kontrakta: str) -> str:
    """Нормализация NOMER_KONTRAKTA перед поиском на портале.

    Это частая причина ложного «не найдено», поэтому нормализация вынесена
    в отдельную переиспользуемую функцию, а не разбросана по парсерам:
    - обрезка пробелов по краям и схлопывание внутренних пробелов
    - унификация дефисов/тире (-, –, —) к обычному дефису
    - убираем пробелы вокруг дефисов ("123 - 45" -> "123-45")
    - убираем ведущие нули в числовых сегментах, разделённых дефисом
      (кроме сегментов, состоящих полностью из нулей)
    """
    if not nomer_kontrakta:
        return nomer_kontrakta

    value = nomer_kontrakta.strip()
    value = _MULTISPACE_RE.sub(" ", value)
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s*-\s*", "-", value)

    def _strip_leading_zeros(segment: str) -> str:
        if segment.isdigit() and len(segment) > 1:
            stripped = segment.lstrip("0")
            return stripped if stripped else "0"
        return segment

    value = "-".join(_strip_leading_zeros(seg) for seg in value.split("-"))
    return value
