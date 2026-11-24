"""만개의 레시피(10000recipe.com) 어댑터.

v1은 페이지를 PDF로 찍고 LLM으로 다시 구조화했다. 레시피 페이지에는 schema.org Recipe
JSON-LD가 들어 있으므로, v2는 HTML을 받아 이것을 코드로 읽는다 (LLM 호출 없음).

사용자가 쓴 글이라 RAG 검색(출처 링크 표시)에만 쓰고 학습 데이터에는 넣지 않는다.
v1의 요청 간격(목록 2.5초, 레시피 5초)과 User-Agent 표시는 그대로 지킨다.
"""

import json
import re
import urllib.parse

import httpx

from ..amounts import parse_line
from ..records import SourceRecipe, Usage
from ..store import Store
from ..titles import clean_title
from .base import Sleep, Source
from .keywords import FOOD_KEYWORDS

BASE = "https://www.10000recipe.com"
_ID_LINK = re.compile(r'href="/recipe/(\d+)"')
_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?$")
_YIELD = re.compile(r"(\d+)")


def _minutes(iso: str | None) -> int | None:
    m = _DURATION.match(iso or "")
    if not m or not any(m.groups()):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def _recipe_ld(html: str) -> dict | None:
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Recipe":
                return item
    return None


def _steps(instructions) -> list[str]:
    steps = []
    for s in instructions or []:
        text = s.get("text", "") if isinstance(s, dict) else str(s)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            steps.append(text)
    return steps


class ManRecipe(Source):
    name = "10000recipe"
    raw_ext = "html"
    usage = Usage(
        rag=True, train=False, license="사용자 저작물. 출처 링크와 함께 검색에만 사용, 학습·재배포 안 함"
    )
    list_delay = 2.5
    fetch_delay = 5.0

    def discover(
        self,
        client: httpx.Client,
        store: Store,
        sleep: Sleep,
        keywords: list[str] | None = None,
        pages: int = 2,
        **_,
    ) -> int:
        added = 0
        for keyword in keywords or FOOD_KEYWORDS:
            for page in range(1, pages + 1):
                url = f"{BASE}/recipe/list.html?q={urllib.parse.quote(keyword)}&page={page}"
                resp = client.get(url)
                resp.raise_for_status()
                ids = list(dict.fromkeys(_ID_LINK.findall(resp.text)))
                sleep(self.list_delay)
                if not ids:  # 결과 끝, 또는 차단
                    break
                added += store.add_listed(self.name, ids, found_by=keyword)
        return added

    def fetch(self, client: httpx.Client, source_id: str) -> bytes:
        resp = client.get(f"{BASE}/recipe/{source_id}")
        resp.raise_for_status()
        return resp.content

    def parse(self, source_id: str, raw: bytes) -> SourceRecipe | None:
        ld = _recipe_ld(raw.decode("utf-8", errors="replace"))
        if not ld:
            return None
        ingredients = [parse_line(x) for x in ld.get("recipeIngredient") or [] if str(x).strip()]
        steps = _steps(ld.get("recipeInstructions"))
        if not ingredients or not steps:
            return None
        rating = ld.get("aggregateRating") or {}
        servings = _YIELD.search(str(ld.get("recipeYield") or ""))
        return SourceRecipe(
            source=self.name,
            source_id=source_id,
            url=f"{BASE}/recipe/{source_id}",
            title=clean_title(str(ld.get("name", ""))),
            title_raw=str(ld.get("name", "")).strip(),
            description=str(ld.get("description", "")).strip(),
            servings=int(servings.group(1)) if servings else None,
            cook_time=_minutes(ld.get("totalTime")),
            ingredients=ingredients,
            steps=steps,
            rating=float(rating["ratingValue"]) if rating.get("ratingValue") else None,
            rating_count=int(rating["reviewCount"]) if rating.get("reviewCount") else None,
            published=ld.get("datePublished"),
            usage=self.usage,
        )
