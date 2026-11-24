"""식약처 식품안전나라 조리식품 레시피 DB (Open API 서비스 COOKRCP01) 어댑터.

목록 응답에 레시피 원본이 함께 오므로 discover에서 원본까지 저장한다.
1인분 영양 정보(열량, 탄수화물, 단백질, 지방, 나트륨, 중량)가 함께 온다.

API 키는 식품안전나라에서 받아 환경 변수 `FOODSAFETY_API_KEY`에 둔다.
공공데이터라 RAG와 학습에 모두 쓰는 것으로 표시한다. 이용 조건은 키를 받을 때 다시 확인한다.
"""

import json
import os
import re

import httpx

from ..amounts import parse_line
from ..records import IngredientLine, Nutrition, SourceRecipe, Usage
from ..store import Store
from .base import Sleep, Source

API = "http://openapi.foodsafetykorea.go.kr/api/{key}/COOKRCP01/json/{start}/{end}"
PAGE_SIZE = 100
_QUALIFIER_END = re.compile(r"(약간|조금|적당량|적당히|소량)$")
_STEP_NUMBER = re.compile(r"^\s*\d+\s*[.)]\s*")
_STEP_TAIL = re.compile(r"[a-z]$")  # 원본 일부 단계 끝에 붙은 한 글자 영문(사진 표시)


def _split_outside_parens(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(depth - 1, 0)
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_parts(details: str) -> list[IngredientLine]:
    """RCP_PARTS_DTLS를 재료 줄로 나눈다.

    원본은 '요리 이름' 줄, '고명'·'양념장' 같은 소제목 줄, '재료 양, 재료 양' 줄이 섞여 있다.
    숫자도 쉼표도 없는 줄은 소제목으로 보고 건너뛴다.
    """
    lines = []
    for line in re.split(r"[\n\r]+", details or ""):
        line = line.strip()
        if ":" in line:
            line = line.split(":", 1)[1].strip()
        if not line:
            continue
        if "," not in line and not re.search(r"\d", line) and not _QUALIFIER_END.search(line):
            continue
        lines.extend(parse_line(p) for p in _split_outside_parens(line))
    return [x for x in lines if x.name]


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FoodSafety(Source):
    name = "foodsafety"
    raw_ext = "json"
    usage = Usage(rag=True, train=True, license="공공데이터 (식품안전나라 Open API). 출처 표시")
    list_delay = 1.0

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("FOODSAFETY_API_KEY", "")

    def discover(
        self, client: httpx.Client, store: Store, sleep: Sleep, limit: int | None = None, **_
    ) -> int:
        if not self.api_key:
            raise RuntimeError(
                "FOODSAFETY_API_KEY가 없습니다. 식품안전나라에서 받은 키를 환경 변수에 넣어 주세요."
            )
        added, start = 0, 1
        while limit is None or start <= limit:
            end = start + PAGE_SIZE - 1 if limit is None else min(start + PAGE_SIZE - 1, limit)
            resp = client.get(API.format(key=self.api_key, start=start, end=end))
            resp.raise_for_status()
            body = resp.json().get("COOKRCP01", {})
            code = (body.get("RESULT") or {}).get("CODE", "")
            rows = body.get("row") or []
            if code != "INFO-000" or not rows:
                break
            for row in rows:
                rid = str(row["RCP_SEQ"])
                if store.add_listed(self.name, [rid], found_by="COOKRCP01"):
                    added += 1
                store.save_raw(self.name, rid, self.raw_ext, json.dumps(row, ensure_ascii=False).encode())
                store.mark(self.name, rid, "fetched")
            sleep(self.list_delay)
            if end >= int(body.get("total_count") or 0):
                break
            start = end + 1
        return added

    def parse(self, source_id: str, raw: bytes) -> SourceRecipe | None:
        row = json.loads(raw)
        steps = []
        for n in range(1, 21):
            text = (row.get(f"MANUAL{n:02d}") or "").strip()
            text = _STEP_TAIL.sub("", _STEP_NUMBER.sub("", text)).strip()
            if text:
                steps.append(text)
        ingredients = parse_parts(row.get("RCP_PARTS_DTLS", ""))
        if not ingredients or not steps:
            return None
        return SourceRecipe(
            source=self.name,
            source_id=source_id,
            url=None,
            title=(row.get("RCP_NM") or "").strip(),
            description=(row.get("RCP_NA_TIP") or "").strip(),
            servings=1,  # 영양 정보가 1인분 기준
            ingredients=ingredients,
            steps=steps,
            nutrition=Nutrition(
                kcal=_float(row.get("INFO_ENG")),
                carbohydrate_g=_float(row.get("INFO_CAR")),
                protein_g=_float(row.get("INFO_PRO")),
                fat_g=_float(row.get("INFO_FAT")),
                sodium_mg=_float(row.get("INFO_NA")),
                weight_g=_float(row.get("INFO_WGT")),
            ),
            usage=self.usage,
        )
