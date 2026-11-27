"""레시피 제목에서 요리 이름만 남기는 규칙.

만개의 레시피 제목에는 꾸밈말이 많다.
예: '[찌개요리]된장찌개 맛있게 끓이는법,된장찌개 만드는법', '봄 향기가 가득한~ 냉이된장찌개'.
v1은 이것을 LLM으로 지웠다. v2는 규칙으로 지우고, 원래 제목은 `title_raw`에 남긴다.

순서:
1. [말머리], 이모티콘, 특수기호를 지운다.
2. 쉼표, '/', ' - ', '|'로 나눈 조각 중 요리 이름이 들어 있는 첫 조각을 고른다.
3. 끝에 붙은 '레시피', '만들기', '끓이는법' 같은 꼬리말과 꾸밈말을 지운다.
4. 끝에서부터 명사 단어를 최대 두 개 모은다. 꾸밈말('맛있는', '구수한')이나 잡음어('백종원')를 만나면 멈춘다.
5. 아무것도 안 남으면 요리 이름이 든 단어에서 조사를 떼어 쓰고, 그것도 없으면 1번 결과를 쓴다.
"""

import re

from .keywords import FOOD_KEYWORDS

MAX_WORDS = 2

_DISH_HINTS = tuple(
    dict.fromkeys(
        [
            "찌개", "국", "탕", "전골", "볶음", "조림", "구이", "무침", "전", "밥", "면", "국수", "찜",
            "말이", "잡채", "샐러드", "김치", "죽", "파스타", "스테이크", "볶이", "만두", "카레", "피자",
            "케이크", "쿠키", "빵", "수프", "스프", "리조또", "나물", "튀김", "까스", "커틀릿", "샌드위치",
            "버거", "라면", "우동", "장아찌", "스무디", "주스", "라떼", "에이드", "떡", "전병", "부침개",
            "갈비", "강정", "덮밥", "김밥", "머핀", "브라우니", "그라탱", "그라탕", "냉채", "수육", "족발",
            "보쌈", "깍두기", "맛탕", "타코", "부리또",
            *FOOD_KEYWORDS,
        ]
    )
)  # fmt: skip

_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|<[^>]*>|\{[^}]*\}")
_EMOTICON = re.compile(r"[:;][-~]?[)(DPp]|\^+|ㅎ+|ㅋ+|ㅠ+|ㅜ+")
_SYMBOLS = re.compile(r"[^\w\s,/|\-·.&+()!:#~]")
# 조각 나누기: 쉼표, /, |, -, ·, !, ::, #, ~, +, 말줄임표, 문장 끝 마침표, 구분자로 쓴 소문자 l
_SPLIT = re.compile(
    r"\s*(?:,|/|\||\s[-–]+\s?|[-–]{2,}|(?<=\S)-(?=\S)|·|!+|:+|#|~+|\+|\.{2,}|\.(?=\s|$)|\sl\s)\s*"
)
_PARENS = re.compile(r"\([^)]*\)")

# '고등어', '연어'처럼 '어'로 끝나는 재료 이름 (꾸밈말 어미 '어'와 구분)
_EO_FOODS = {
    *("고등어", "연어", "문어", "오징어", "장어", "광어", "숭어"),
    *("전어", "붕어", "잉어", "홍어", "민어", "날치알"),
}
# '면'으로 끝나지만 요리가 아닌 말 ('알면', '보면')
_MYEON_CLAUSES = {"알면", "보면", "되면", "오면", "가면", "먹으면", "있으면", "없으면", "싶으면", "해보면"}
# '국', '전', '면'으로 끝나지만 요리가 아닌 말
_NOT_DISH = {"중국", "한국", "미국", "외국", "천국", "전국", "결국", "도전", "면", "전"}

# 끝에 붙는 꼬리말 ('된장찌개 만드는 법')
_TAIL = re.compile(
    r"(레시피|만들기|끓이기|굽기|볶기|만드는법|끓이는법|굽는법|하는법|요리법|법|방법|만드는|끓이는|버전|ver\.?|"
    r"대박|강추|추천|완성|성공|꿀팁|간단|초간단|초스피드|부럽지않아요|부럽지 않아요)$",
    re.IGNORECASE,
)
# 이름 앞에 오면 거기서 멈추는 잡음어 (방송·인물·홍보 말)
_NOISE = re.compile(
    r"(레시피|백종원|백선생|정보통|수미네|알토란|편스토랑|자취|꿀팁|밥도둑|맛집|인생|초간단|간단|초스피드|"
    r"매콤|달콤|새콤|짭짤|바삭|쫄깃|든든|후다닥|뚝딱|한끼|강추|대박|엄마표|아빠표|메뉴|^굿$|\d+|"
    r"이연복|백주부|집밥|^l$|^완전$|^진짜$|^정말$|^너무$|^요리$|^반찬$|^기본$|^맛$|^스타일$|^인기$)",
)
# 꾸밈말 어미 ('맛있는', '구수한', '맛있게', '끓이는', '만나면', '두부만')
_MODIFIER = re.compile(
    r"(한|는|은|운|던|된|인|준|든|낸|히|로|으로|에|에서|를|을|만|요|다|면서|나면|으면|하면|아|어|서|와|과|의|"
    r"엔|려|하게|있게|롭게|럽게|없게|쉽게|맵게|짜게|달게|없이|가득)$"
)
_PARTICLE = re.compile(r"(을|를|이|가|은|는|와|과|에|로|으로|의|도|만|처럼|보다)$")
_CLAUSE = re.compile(r"(나면|으면|하면|면서|다면)$")  # '만나면'은 '면'으로 끝나도 요리가 아니다
# '속이 꽉 찬 김밥'의 '찬'처럼 한 글자 꾸밈말
_SHORT_MODIFIERS = {"찬", "든", "썬", "탄", "간", "꽉", "더", "또", "딱", "쏙", "확", "참"}
# 요리 이름에 붙어 쓴 꼬리말 ('잡채만들기', '잡채맛있게하는법')
_GLUED_TAIL = re.compile(
    r"(맛있게|간단하게|쉽게)?(하는법|만드는법|끓이는법|[가-힣]는법|만드는방법|방법|만들기|끓이기|황금레시피|레시피)$"
)


def _is_modifier(word: str) -> bool:
    return bool(_MODIFIER.search(word)) and word not in _EO_FOODS


def _with_particle(word: str) -> bool:
    """'라면이', '전골이'처럼 요리 이름 뒤에 조사가 붙었나. '떡볶이', '계란말이'는 아니다."""
    m = re.search(r"(이|가|은|는|을|를|처럼)$", word)
    stem = word[: m.start()] if m else ""
    return len(stem) >= 2 and any(stem.endswith(h) for h in _DISH_HINTS)


def _clean_dish(word: str) -> bool:
    return _has_dish(word) and not _is_modifier(word) and not _with_particle(word)


def _has_dish(text: str) -> bool:
    if _CLAUSE.search(text) or text in _MYEON_CLAUSES or text in _NOT_DISH:
        return False
    stem = _PARTICLE.sub("", text)
    # 한 글자 힌트('국', '면', '전')는 단어 끝에 있을 때만 요리로 본다
    return any(stem.endswith(h) if len(h) == 1 else h in text for h in _DISH_HINTS)


def _strip(raw: str) -> str:
    text = _BRACKETS.sub(" ", raw)
    text = _EMOTICON.sub(" ", text)
    text = _SYMBOLS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ,/|-·.")


def _unglue(word: str) -> str:
    stem = _GLUED_TAIL.sub("", word)
    return stem if stem != word and _has_dish(stem) else word


def _bracket_texts(raw: str) -> list[str]:
    return [_strip(m.group(0)[1:-1]) for m in _BRACKETS.finditer(raw)]


def clean_title(raw: str) -> str:
    base = _strip(raw)
    # 괄호 밖에 요리 이름이 있으면 괄호 안은 버린다 ('((집밥 백선생)) 닭갈비', '칼국수면 (칼국수/반죽)')
    outside = re.sub(r"\s+", " ", _PARENS.sub(" ", _PARENS.sub(" ", base))).strip()
    if any(_has_dish(w) for w in outside.split()):
        base = outside
    # 말머리 밖에 요리 이름이 없으면 말머리 안을 쓴다
    # ('매일 먹어도 좋은 [차돌박이 된장찌개]', '【… 만드는 방법】')
    if not any(_has_dish(w) for w in base.split()):
        inside = next((t for t in _bracket_texts(raw) if any(_has_dish(w) for w in t.split())), "")
        base = inside or base
    if not base:
        return raw.strip()
    parts = [p for p in _SPLIT.split(base) if p.strip()]
    # 조사 없이 요리 이름이 든 조각을 먼저 고른다 ('전골이 필요한 계절~ 뜨끈한 불고기버섯전골')
    segment = next(
        (p for p in parts if any(_clean_dish(w) and len(w) >= 2 for w in p.split())),
        next(
            (p for p in parts if any(_has_dish(w) for w in p.split())),
            parts[0] if parts else base,
        ),
    )
    words = [_unglue(w) for w in _PARENS.sub(" ", segment).replace("(", " ").replace(")", " ").split()]

    # 마지막 요리 단어 뒤는 모두 꼬리말로 본다 ('닭볶음탕 만들기 쉽고 맛있기까지 하네요')
    # 조사가 붙은 요리 단어('삼겹살은')보다 조사 없는 요리 단어를 먼저 본다
    dish_at = [i for i, w in enumerate(words) if _clean_dish(w)]
    dish_at = dish_at or [i for i, w in enumerate(words) if _has_dish(w)]
    if dish_at:
        words = words[: dish_at[-1] + 1]
    while words and not _has_dish(words[-1]) and (_TAIL.search(words[-1]) or _is_modifier(words[-1])):
        words.pop()

    kept: list[str] = []
    for w in reversed(words):
        if (
            len(kept) >= MAX_WORDS
            or _NOISE.search(w)
            or w in _SHORT_MODIFIERS
            or _TAIL.fullmatch(w)  # 가운데 낀 꼬리말 ('오므라이스 만드는법 오므라이스')
            or (_is_modifier(w) and not _has_dish(w))
        ):
            break
        if _is_modifier(w):  # '된장찌개를'처럼 조사가 붙은 요리 이름은 거기서 끝
            kept.append(_PARTICLE.sub("", w))
            break
        kept.append(w)
    name = " ".join(reversed(kept)).strip(" .-&+~#")
    if name:
        return name

    for w in words:  # '당면이 된장찌개를 만나면'
        if _has_dish(w):
            return _PARTICLE.sub("", w)
    return segment.strip() or base
