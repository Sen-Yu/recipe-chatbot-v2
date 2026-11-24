"""후보 선택: 입력 게이트와 결정 모델의 규칙 단계 (설계 기록 4.7, 초안 E1).

입력 게이트는 '선택지가 떠 있고, 발화가 선택처럼 보이는가'만 코드로 본다.
결정 모델은 규칙으로 번호를 정하고, 규칙으로 못 정하면 작은 모델에 묻고, 그래도 모르면 되묻는다.
게이트가 애매하면 수퍼바이저로 보낸다. 어느 쪽으로 틀려도 응답이 망가지지 않게 하기 위해서다.
"""

import re
from dataclasses import dataclass
from typing import Literal

from .domain.ingredient_synonyms import normalize_ingredient_name

MAX_GATE_LENGTH = 30  # 이보다 긴 발화는 여러 의도가 섞였을 가능성이 커서 게이트에서 받지 않는다

_UNIT = r"(개|g|kg|ml|큰술|작은술|컵|공기|모|대|근|봉지|캔|쪽|인분|분|시간|T|t)"
_AMOUNT = re.compile(rf"\d+(\.\d+)?\s*{_UNIT}")
_EXCLUDE_WORDS = (
    "다른", "추천", "칼로리", "kcal", "알레르기", "단백질", "맵게", "덜 ", "짜게", "달게", "싱겁게",
    "빼고", "빼줘", "추가", "넣어", "있어", "없어", "레시피 알려",
)
_NUMBER = re.compile(r"(\d+)\s*(번째|번)?")
_ORDINALS = {"첫": 1, "두": 2, "세": 3, "네": 4}
_ORDINAL = re.compile(r"(첫|두|세|네)\s*(번째|번|째)")
_LAST = re.compile(r"마지막")
_SELECT_VERB = re.compile(r"(할게|할래|로 해|으로 해|줘|골라|선택|먹을래|이걸로|그걸로|이거|그거|걸로)")


@dataclass(frozen=True)
class Resolution:
    kind: Literal["one", "many", "unknown"]
    number: int | None = None  # kind == "one" 일 때 1부터 시작하는 번호


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _title_hits(utterance: str, titles: list[str]) -> list[int]:
    """발화에 들어 있는 후보 제목의 번호 목록.

    제목 전체가 들어 있으면 맞은 것으로 본다 (띄어쓰기 무시, 재료명 동의어 정규화).
    제목 전체가 없어도, 제목의 마지막 단어(보통 요리 이름)가 발화에 있고 그 단어를 가진 후보가
    하나뿐이면 맞은 것으로 본다. 예: '두부조림' → '돼지고기 두부조림'.
    """
    u = _squash(utterance)
    last_words = [t.split()[-1] if t.split() else "" for t in titles]
    hits = []
    for n, title in enumerate(titles, start=1):
        t = _squash(title)
        t_norm = _squash(" ".join(normalize_ingredient_name(w) for w in title.split()))
        last = last_words[n - 1]
        full = len(t) >= 2 and (t in u or t_norm in u)
        partial = len(last) >= 2 and last in u and last_words.count(last) == 1
        if full or partial:
            hits.append(n)
    # 짧은 제목이 긴 제목 안에 들어 있으면('김치볶음밥' ⊂ '참치 김치볶음밥') 긴 쪽만 남긴다
    squashed = {n: _squash(titles[n - 1]) for n in hits}
    return [n for n in hits if not any(m != n and squashed[n] in squashed[m] for m in hits)]


def _numbers(utterance: str, count: int) -> list[int]:
    """발화에 나온 후보 번호들 (나온 순서대로, 범위 밖 숫자와 재료 양은 뺀다)."""
    text = _AMOUNT.sub(" ", utterance)
    found: list[tuple[int, int]] = []
    for m in _NUMBER.finditer(text):
        found.append((m.start(), int(m.group(1))))
    for m in _ORDINAL.finditer(text):
        found.append((m.start(), _ORDINALS[m.group(1)]))
    for m in _LAST.finditer(text):
        found.append((m.start(), count))
    found.sort()
    return [n for _, n in found if 1 <= n <= count]


def is_selection_like(utterance: str, titles: list[str]) -> bool:
    """입력 게이트: 결정 모델로 보낼지 판단한다. 선택지가 없으면 항상 False."""
    if not titles:
        return False
    text = utterance.strip()
    if not text or len(text) > MAX_GATE_LENGTH:
        return False
    if _AMOUNT.search(text) or any(w in text for w in _EXCLUDE_WORDS):
        return False
    if _numbers(text, len(titles)) or _title_hits(text, titles):
        return True
    return bool(_SELECT_VERB.search(text))


def resolve(utterance: str, titles: list[str]) -> Resolution:
    """결정 모델의 규칙 단계: 고른 번호를 정한다. 못 정하면 unknown."""
    text = utterance.strip()
    if "말고" in text:
        # "2번 말고 3번" → '말고' 뒤쪽이 최종 선택
        text = text.rsplit("말고", 1)[1]
    picked = list(dict.fromkeys(_numbers(text, len(titles)) + _title_hits(text, titles)))
    if len(picked) == 1:
        return Resolution("one", picked[0])
    if len(picked) > 1:
        return Resolution("many")
    return Resolution("unknown")
