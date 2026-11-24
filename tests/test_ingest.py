"""레시피 수집 테스트. 사이트에는 요청하지 않는다 (가짜 HTTP와 직접 만든 원본만 사용)."""

import json

import httpx
import pytest

from recipe_chatbot.ingest import pipeline, refine
from recipe_chatbot.ingest.amounts import parse_line
from recipe_chatbot.ingest.sources import FoodSafety, ManRecipe
from recipe_chatbot.ingest.store import Store
from recipe_chatbot.ingest.titles import clean_title


def recipe_page(name: str, ingredients: list[str], steps: list[str], reviews: int = 3) -> str:
    ld = {
        "@context": "http://schema.org/",
        "@type": "Recipe",
        "name": name,
        "description": "테스트용 설명",
        "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.5", "reviewCount": str(reviews)},
        "totalTime": "PT1H15M",
        "recipeYield": "2 servings",
        "recipeIngredient": ingredients,
        "recipeInstructions": [{"@type": "HowToStep", "text": s} for s in steps],
    }
    script = f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'
    return f"<html><head>{script}</head></html>"


LIST_PAGE = '<a href="/recipe/101">a</a><a href="/recipe/101">a</a><a href="/recipe/102">b</a>'
PAGES = {
    "101": recipe_page(
        "테스트 감자조림",
        ["감자 2개", "간장 3큰술", "설탕 1/2큰술", "물 1컵(200ml)"],
        ["감자를 썬다.", "졸인다."],
    ),
    "102": recipe_page("공지사항", [], []),
}
FS_ROW = {
    "RCP_SEQ": "900",
    "RCP_NM": "테스트 두부찜",
    "RCP_PARTS_DTLS": (
        "테스트 두부찜\n두부 150g(1/2모), 대파 10g(1/4대), 간장 5g(1작은술)\n양념장\n참기름 약간"
    ),
    "MANUAL01": "1. 두부를 썬다.a",
    "MANUAL02": "2. 양념장을 끼얹어 찐다.b",
    "MANUAL03": "",
    "INFO_ENG": "210",
    "INFO_PRO": "15.2",
    "INFO_NA": "",
    "RCP_NA_TIP": "나트륨을 줄였다.",
}


def fake_site(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/recipe/list.html":
        return httpx.Response(200, text=LIST_PAGE if request.url.params["page"] == "1" else "")
    rid = path.rsplit("/", 1)[-1]
    if rid in PAGES:
        return httpx.Response(200, text=PAGES[rid])
    if "COOKRCP01" in path:
        return httpx.Response(
            200, json={"COOKRCP01": {"total_count": "1", "row": [FS_ROW], "RESULT": {"CODE": "INFO-000"}}}
        )
    return httpx.Response(404)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ingest")
    yield s
    s.close()


@pytest.fixture
def client():
    with pipeline.make_client(httpx.MockTransport(fake_site)) as c:
        yield c


def no_sleep(_):
    pass


@pytest.mark.parametrize(
    ("raw", "name", "amount", "unit", "grams", "parsed"),
    [
        ("삼겹살 1/2줌 (100g)", "삼겹살", 0.5, "줌", 100.0, True),
        ("간장 0.5큰술", "간장", 0.5, "큰술", None, True),
        ("연두부 75g(3/4모)", "연두부", 75.0, "g", 75.0, True),
        ("고기 200~300g", "고기", 250.0, "g", 250.0, True),
        ("설탕 1 1/2큰술", "설탕", 1.5, "큰술", None, True),
        ("대파 반개", "대파", 0.5, "개", None, True),
        ("소고기 0.5kg", "소고기", 0.5, "kg", 500.0, True),
        ("소금 약간", "소금", None, "약간", None, True),
        ("후춧가루", "후춧가루", None, "", None, True),
        ("다진 마늘 1큰술", "다진마늘", 1.0, "큰술", None, True),  # 띄어쓰기 통일
        ("1/2개", "1/2개", None, "", None, False),
        ("대파 적당량 (생략 가능)", "대파", None, "적당량", None, True),
        ("청양고추 2~3개 (선택)", "청양고추", 2.5, "개", None, True),
        ("소금 간맞추기 ", "소금", None, "간맞추기", None, True),
        ("양파(소)1개", "양파", 1.0, "개", None, True),
        ("물(종이컵 1컵이 180ml) 400ml", "물", 400.0, "ml", None, True),
        ("양파 큰것 1/2개", "양파", 0.5, "개", None, True),
        ("고추장 듬뿍 1스푼", "고추장", 1.0, "큰술", None, True),
        ("갈비 찜용 1.5근", "갈비", 1.5, "근", None, True),
        ("김밥용 단무지 4개", "김밥용 단무지", 4.0, "개", None, True),
        ("간장 2T", "간장", 2.0, "큰술", None, True),
        ("설탕 1t", "설탕", 1.0, "작은술", None, True),
        ("버터 1Ts", "버터", 1.0, "큰술", None, True),
        ("소금 1ts", "소금", 1.0, "작은술", None, True),
        ("물 300cc", "물", 300.0, "ml", None, True),
        ("고기 200그램", "고기", 200.0, "g", 200.0, True),
        # 3,000개 점검(L.4.9)에서 찾은 예외
        ("김치 1컵 170g", "김치", 1.0, "컵", 170.0, True),  # 양을 두 번 쓴 줄
        ("고사리 200g 1팩", "고사리", 1.0, "팩", 200.0, True),
        ("박력분1 60g", "박력분", 60.0, "g", 60.0, True),  # 묶음 번호
        ("달걀 2개", "계란", 2.0, "개", None, True),  # 같은 재료 다른 이름
    ],
)
def test_parse_line(raw, name, amount, unit, grams, parsed):
    r = parse_line(raw)
    assert (r.name, r.amount, r.unit, r.grams, r.parsed) == (name, amount, unit, grams, parsed)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("감자조림", "감자조림"),
        ("감자조림 만드는 법", "감자조림"),
        ("김치찌개 황금레시피 :-)", "김치찌개"),
        ("유명셰프 백종원 잡채 레시피", "잡채"),
        ("[반찬요리]감자조림 맛있게 만드는법,감자조림 만들기", "감자조림"),
        ("바지락 칼국수 / 식당 부럽지않아요", "바지락 칼국수"),
        ("가지볶음 - 집밥백선생2 가지요리", "가지볶음"),
        ("봄 향기가 가득한~ 달래된장국", "달래된장국"),
        ("국물맛이 끝내주는 꽃게탕", "꽃게탕"),
        ("후다닥 볶아 한끼해결 참치 김치볶음밥", "참치 김치볶음밥"),
        ("♡라면이 떡볶이를 만나면~ 대박~~^^", "떡볶이"),
        ("초간단!! 5분 계란찜", "계란찜"),
        ("엄마표 계란말이", "계란말이"),
        ("매콤한 냉장고 볶음밥", "냉장고 볶음밥"),
        ("비빔면", "비빔면"),
        ("전주 비빔밥", "전주 비빔밥"),
        ("【고깃집 김치찌개 만드는 방법】", "고깃집 김치찌개"),
        ("매일 먹어도 좋은 [바지락 칼국수]", "바지락 칼국수"),
        ("닭볶음탕 만들기 쉽고 맛있기까지 하네요~", "닭볶음탕"),
        ("속이 꽉 찬 김밥", "김밥"),
        ("깔끔한 한정식집 메뉴 참나물무침", "참나물무침"),
        ("김치콩나물 국 얼큰하고 맛나게 끓이기!", "김치콩나물 국"),
        ("엄청쉬운 밥도둑 갈비찜 만능소스", "갈비찜"),
        ("잡채맛있게하는법! 너무간단해", "잡채"),
        ("잡채만들기 황금레시피", "잡채"),
        ("아이반찬으로 굿! 맛살계란말이 만들기", "맛살계란말이"),
        ("초간단 카페 파스타! 휴일에 간편한 한끼로 OK~", "카페 파스타"),
        ("7. 맛있는 미역국 레시피 <미역활용요리 1탄>", "미역국"),
        # 3,000개 점검(L.4.9)에서 찾은 예외
        ("오므라이스 만드는법 오므라이스 황금레시피", "오므라이스"),  # 가운데 낀 꼬리말
        ("((집밥 백선생)) 닭갈비 레시피 ~", "닭갈비"),  # 겹괄호
        ("(황금레시피) 간단하게 만드는 고등어 김치찜", "고등어 김치찜"),  # '어'로 끝나는 재료
        ("전골이 필요한 계절~! 뜨끈한 불고기버섯전골", "불고기버섯전골"),  # 조사 붙은 요리 이름
        ("시원한 오이 미역냉국 :: 황금비율만 알면 문제없어요", "오이 미역냉국"),  # '::' 구분, '알면'
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_manrecipe_parse_json_ld():
    r = ManRecipe().parse("101", PAGES["101"].encode())
    assert r.title == "테스트 감자조림" and r.title_raw == "테스트 감자조림"
    assert (r.servings, r.cook_time, r.rating, r.rating_count) == (2, 75, 4.5, 3)
    assert [i.name for i in r.ingredients] == ["감자", "간장", "설탕", "물"]
    assert r.steps == ["감자를 썬다.", "졸인다."]
    assert r.url == "https://www.10000recipe.com/recipe/101"
    assert (r.usage.rag, r.usage.train) == (True, False)
    assert r.to_structured().recipe_id == "10000recipe:101"


def test_manrecipe_page_without_recipe_is_skipped():
    assert ManRecipe().parse("102", PAGES["102"].encode()) is None
    assert ManRecipe().parse("103", b"<html></html>") is None


def test_pipeline_end_to_end(store, client, tmp_path):
    src = ManRecipe()
    assert pipeline.discover(src, client, store, sleep=no_sleep, keywords=["감자"], pages=3) == 2
    assert (
        pipeline.discover(src, client, store, sleep=no_sleep, keywords=["감자"], pages=1) == 0
    )  # 이미 아는 id

    r = pipeline.fetch(src, client, store, sleep=no_sleep)
    assert (r.done, r.failed) == (2, 0)
    assert pipeline.fetch(src, client, store, sleep=no_sleep).done == 0  # 남은 것 없음

    r = pipeline.parse(src, store)
    assert (r.done, r.skipped) == (1, 1)

    fs = FoodSafety(api_key="k")
    assert pipeline.discover(fs, client, store, sleep=no_sleep) == 1
    assert pipeline.parse(fs, store).done == 1

    assert store.counts() == {"10000recipe": {"parsed": 1, "skipped": 1}, "foodsafety": {"parsed": 1}}

    summary = refine.build(store, tmp_path / "out")
    assert (summary["kept"], summary["rag"], summary["train"]) == (2, 2, 1)
    assert summary["without_amounts"] == 0
    train = [json.loads(x) for x in (tmp_path / "out" / "train.jsonl").read_text().splitlines()]
    assert [x["source"] for x in train] == ["foodsafety"]


def test_fetch_stops_after_consecutive_failures(store, client):
    src = ManRecipe()
    store.add_listed(src.name, [str(i) for i in range(500, 510)])
    r = pipeline.fetch(src, client, store, sleep=no_sleep)
    assert r.failed == pipeline.MAX_CONSECUTIVE_FAILS and r.stopped
    assert len(store.ids(src.name, "listed")) == 10 - pipeline.MAX_CONSECUTIVE_FAILS


def test_fetch_waits_between_requests(store, client):
    src, waits = ManRecipe(), []
    store.add_listed(src.name, ["101", "102"])
    pipeline.fetch(src, client, store, sleep=waits.append)
    assert waits == [5.0, 5.0]


def test_reparse_uses_saved_raw(store, client):
    src = ManRecipe()
    store.add_listed(src.name, ["101"])
    pipeline.fetch(src, client, store, sleep=no_sleep)
    pipeline.parse(src, store)
    assert pipeline.parse(src, store, reparse=True).done == 1  # 사이트에 다시 요청하지 않음


def test_dedup_prefers_trainable_source(store):
    a = ManRecipe().parse(
        "1", recipe_page("두부찜", ["두부 1모", "대파 1대"], ["썬다", "찐다"], reviews=99).encode()
    )
    b = a.model_copy(
        update={"source": "foodsafety", "source_id": "2", "usage": FoodSafety.usage, "rating_count": 0}
    )
    store.save_parsed(a)
    store.save_parsed(b)
    summary = refine.build(store)
    assert summary["duplicates"] == 1 and summary["by_source"] == {"foodsafety": 1}


def test_refine_rejects_short_recipes(store):
    r = ManRecipe().parse("1", recipe_page("짧은 요리", ["두부 1모", "대파 1대"], ["끝"]).encode())
    store.save_parsed(r)
    assert refine.build(store)["rejected"] == {"조리 단계 2개 미만": 1}
