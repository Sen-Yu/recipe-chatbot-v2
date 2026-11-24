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
    "간맞추기",
    "원하는만큼",
    "기호에맞게",
    "기호껏",
    "입맛대로",
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
_NOTE = re.compile(r"^(?P<main>.*?)\s*\((?P<note>[^)]*)\)\s*$")  # 끝에 붙은 괄호 메모


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


# 재료 이름 뒤에 붙은 크기·양·용도 표현 ('양파 큰것', '고추장 듬뿍', 'LA갈비 찜용')
_NAME_TAIL = re.compile(
    r"\s+(큰\s?것|작은\s?것|중간\s?것|중간|큰|작은|중|대|소|듬뿍|크게|작게|넉넉히|조금|약간|\S+용)$"
)
# 같은 단위의 여러 표기를 하나로 ('T', 'Ts', '스푼', '숟가락' → '큰술'). 대소문자를 구분한다: T=큰술, t=작은술
_UNIT_ALIASES = {
    **dict.fromkeys(
        ["T", "Ts", "TS", "Tbsp", "tbsp", "Tb"]
        + ["스푼", "큰스푼", "숟가락", "숟갈", "밥숟가락", "밥스푼", "수저"],
        "큰술",
    ),
    **dict.fromkeys(["t", "ts", "tsp", "티스푼", "작은스푼", "찻숟가락", "커피스푼"], "작은술"),
    **dict.fromkeys(["cc", "CC", "mL", "ML", "㎖"], "ml"),
    **dict.fromkeys(["G", "그램", "그람"], "g"),
    **dict.fromkeys(["C", "cup"], "컵"),
}


def normalize_unit(unit: str) -> str:
    return _UNIT_ALIASES.get(unit, unit)


# 같은 재료의 맞춤법·띄어쓰기 차이만 하나로 ('고추가루' → '고춧가루', '다진 마늘' → '다진마늘').
# 뜻이 다른 재료(후추와 후춧가루 등)는 합치지 않는다.
_NAME_ALIASES = {
    "고추가루": "고춧가루",
    "달걀": "계란",
    "계란노른자": "계란 노른자",
    "달걀노른자": "계란 노른자",
}
_TWO_AMOUNTS = re.compile(rf"^(?P<name>.*?)\s*(?P<num>{_NUM})\s*(?P<unit>[가-힣A-Za-z]+)$")


def _canonical(name: str) -> str:
    name = re.sub(r"^다진\s+", "다진", name)
    return _NAME_ALIASES.get(name, name)


def _clean_name(name: str) -> str:
    name = re.sub(r"\s*\([^)]*\)\s*", " ", name)  # '양파(소)' → '양파'
    name = re.sub(r"\s+", " ", _BULLET.sub("", name)).strip(" ,:")
    name = re.sub(r"(?<=[가-힣A-Za-z])\d$", "", name)  # 재료 묶음 번호 ('박력분1', '박력분2')
    stripped = _NAME_TAIL.sub("", name)
    return _canonical(stripped or name)


def parse_line(raw: str) -> IngredientLine:
    text = _BULLET.sub("", raw).strip()
    note = _NOTE.match(text)
    if note and note.group("main") and not re.search(r"\d", note.group("note")):
        text = note.group("main")  # '(생략 가능)'처럼 숫자 없는 메모는 버린다. '(100g)'은 남긴다
    if not text:
        return IngredientLine(raw=raw, name="", parsed=False)

    m = _AMOUNT_LINE.match(text)
    if m and _clean_name(m.group("name")):
        amount = _number(m.group("num"))
        if m.group("num2"):
            amount = (amount + _number(m.group("num2"))) / 2  # 범위는 가운데 값
        unit = normalize_unit(m.group("unit") or "")
        name, grams = m.group("name"), _grams(amount, unit, m.group("paren"))
        # 양을 두 번 쓴 줄: 하나는 그램, 하나는 단위 ('김치 1컵 170g', '고사리 200g 1팩')
        two = _TWO_AMOUNTS.match(name)
        if two and _clean_name(two.group("name")):
            inner = _number(two.group("num"))
            inner_unit = normalize_unit(two.group("unit"))
            inner_grams = _grams(inner, inner_unit, None)
            if grams is not None and inner_grams is None:
                name, amount, unit = two.group("name"), inner, inner_unit
            elif inner_grams is not None and grams is None:
                name, grams = two.group("name"), inner_grams
        return IngredientLine(
            raw=raw,
            name=_clean_name(name),
            amount=round(amount, 2),
            unit=unit,
            grams=grams,
        )

    m = _KOREAN_LINE.match(text)
    if m:
        amount = _KOREAN_NUMBERS[m.group("k")]
        return IngredientLine(
            raw=raw,
            name=_clean_name(m.group("name")),
            amount=amount,
            unit=normalize_unit(m.group("unit")),
            grams=_grams(amount, normalize_unit(m.group("unit")), m.group("paren")),
        )

    words = text.split()
    if len(words) >= 2 and any(words[-1].endswith(q) for q in _QUALIFIERS):
        return IngredientLine(raw=raw, name=_clean_name(" ".join(words[:-1])), unit=words[-1])

    if re.search(r"\d", text):
        return IngredientLine(raw=raw, name=_clean_name(text), parsed=False)
    return IngredientLine(raw=raw, name=_clean_name(text))  # '소금'처럼 양이 없는 재료
