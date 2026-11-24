"""재료 한 줄('삼겹살 1/2줌 (100g)')을 이름·양·단위·그램으로 나누는 규칙.

규칙으로 못 나눈 줄은 parsed=False로 남긴다. 그런 줄만 나중에 작은 모델로 보낸다.
"""

import re

from .records import IngredientLine

_FRACTIONS = {"½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25, "¾": 0.75}
_KOREAN_NUMBERS = {"한": 1.0, "두": 2.0, "세": 3.0, "네": 4.0, "반": 0.5}
_QUALIFIERS = (
    "약간",
    "조금",
    "적당량",
    "적당히",
    "소량",
    "넉넉히",
    "취향껏",
    "톡톡",
    "살짝",
    "적량",
    "충분히",
)

_NUM = r"\d+(?:\.\d+)?(?:\s*/\s*\d+)?|[½⅓⅔¼¾]"
_AMOUNT_LINE = re.compile(
    rf"^(?P<name>.*?)\s*(?P<num>(?:\d+\s+(?=\d+\s*/))?(?:{_NUM}))"
    rf"(?:\s*[~\-]\s*(?P<num2>{_NUM}))?"
    r"\s*(?P<unit>[^\s\d()~\-][^\s()]*)?"
    r"\s*(?:\((?P<paren>[^)]*)\))?\s*$"
)
_KOREAN_LINE = re.compile(
    r"^(?P<name>.+?)\s+(?P<k>한|두|세|네|반)\s*(?P<unit>[^\s()]+)\s*(?:\((?P<paren>[^)]*)\))?$"
)
_GRAMS = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|그램)\b", re.IGNORECASE)
_BULLET = re.compile(r"^[\s●○•·\-*]+")


def _number(text: str) -> float:
    text = text.strip()
    if text in _FRACTIONS:
        return _FRACTIONS[text]
    parts = text.split()
    if len(parts) == 2:  # 대분수 '1 1/2'
        return _number(parts[0]) + _number(parts[1])
    if "/" in text:
        a, b = (float(x) for x in text.split("/"))
        return a / b if b else 0.0
    return float(text)


def _grams(amount: float | None, unit: str, paren: str | None) -> float | None:
    u = unit.lower()
    if amount is not None and u in ("g", "그램"):
        return amount
    if amount is not None and u == "kg":
        return amount * 1000
    if paren:
        m = _GRAMS.search(paren)
        if m:
            value = float(m.group(1))
            return value * 1000 if m.group(2).lower() == "kg" else value
    return None


def _clean_name(name: str) -> str:
    return _BULLET.sub("", name).strip(" ,:")


def parse_line(raw: str) -> IngredientLine:
    text = _BULLET.sub("", raw).strip()
    if not text:
        return IngredientLine(raw=raw, name="", parsed=False)

    m = _AMOUNT_LINE.match(text)
    if m and _clean_name(m.group("name")):
        amount = _number(m.group("num"))
        if m.group("num2"):
            amount = (amount + _number(m.group("num2"))) / 2  # 범위는 가운데 값
        unit = m.group("unit") or ""
        return IngredientLine(
            raw=raw,
            name=_clean_name(m.group("name")),
            amount=round(amount, 2),
            unit=unit,
            grams=_grams(amount, unit, m.group("paren")),
        )

    m = _KOREAN_LINE.match(text)
    if m:
        amount = _KOREAN_NUMBERS[m.group("k")]
        return IngredientLine(
            raw=raw,
            name=_clean_name(m.group("name")),
            amount=amount,
            unit=m.group("unit"),
            grams=_grams(amount, m.group("unit"), m.group("paren")),
        )

    words = text.split()
    if len(words) >= 2 and any(words[-1].endswith(q) for q in _QUALIFIERS):
        return IngredientLine(raw=raw, name=_clean_name(" ".join(words[:-1])), unit=words[-1])

    if re.search(r"\d", text):
        return IngredientLine(raw=raw, name=_clean_name(text), parsed=False)
    return IngredientLine(raw=raw, name=_clean_name(text))  # '소금'처럼 양이 없는 재료
