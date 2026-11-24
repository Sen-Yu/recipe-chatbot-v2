"""레시피 수집 테스트. 사이트에는 요청하지 않는다 (가짜 HTTP와 직접 만든 원본만 사용)."""

import json

import httpx
import pytest

from recipe_chatbot.ingest import pipeline, refine
from recipe_chatbot.ingest.__main__ import main as cli
from recipe_chatbot.ingest.amounts import parse_line
from recipe_chatbot.ingest.sources import FoodSafety, ManRecipe
from recipe_chatbot.ingest.sources.foodsafety import parse_parts
from recipe_chatbot.ingest.store import Store


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
        ("다진 마늘 1큰술", "다진 마늘", 1.0, "큰술", None, True),
        ("1/2개", "1/2개", None, "", None, False),
    ],
)
def test_parse_line(raw, name, amount, unit, grams, parsed):
    r = parse_line(raw)
    assert (r.name, r.amount, r.unit, r.grams, r.parsed) == (name, amount, unit, grams, parsed)


def test_manrecipe_parse_json_ld():
    r = ManRecipe().parse("101", PAGES["101"].encode())
    assert r.title == "테스트 감자조림"
    assert (r.servings, r.cook_time, r.rating, r.rating_count) == (2, 75, 4.5, 3)
    assert [i.name for i in r.ingredients] == ["감자", "간장", "설탕", "물"]
    assert r.steps == ["감자를 썬다.", "졸인다."]
    assert r.url == "https://www.10000recipe.com/recipe/101"
    assert (r.usage.rag, r.usage.train) == (True, False)
    assert r.to_structured().recipe_id == "10000recipe:101"


def test_manrecipe_page_without_recipe_is_skipped():
    assert ManRecipe().parse("102", PAGES["102"].encode()) is None
    assert ManRecipe().parse("103", b"<html></html>") is None


def test_foodsafety_parse_parts_skips_headers():
    names = [i.name for i in parse_parts(FS_ROW["RCP_PARTS_DTLS"])]
    assert names == ["두부", "대파", "간장", "참기름"]


def test_foodsafety_parse():
    r = FoodSafety(api_key="k").parse("900", json.dumps(FS_ROW).encode())
    assert r.steps == ["두부를 썬다.", "양념장을 끼얹어 찐다."]
    assert r.nutrition.kcal == 210 and r.nutrition.protein_g == 15.2 and r.nutrition.sodium_mg is None
    assert r.ingredients[0].grams == 150.0
    assert (r.usage.rag, r.usage.train) == (True, True)


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


def test_cli_status_and_build(tmp_path, capsys):
    root = str(tmp_path / "ingest")
    assert cli(["--root", root, "status"]) == 0
    assert cli(["--root", root, "build"]) == 0
    assert '"kept": 0' in capsys.readouterr().out


def test_foodsafety_needs_key(store, client, monkeypatch):
    monkeypatch.delenv("FOODSAFETY_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        FoodSafety().discover(client, store, no_sleep)
