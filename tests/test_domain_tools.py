"""v1에서 옮긴 자산과 코드 도구 (LLM 없음)."""

import pytest

from recipe_chatbot.domain.ingredient_synonyms import normalize_ingredient_name
from recipe_chatbot.schemas import Ingredient, IngredientUpdate, RecipeIngredient, StructuredRecipe
from recipe_chatbot.tools import (
    InMemoryNutritionDB,
    InMemoryRetriever,
    apply_pantry_update,
    calc_nutrition,
    combination_warnings,
    missing_ingredients,
    to_grams,
)

from .conftest import KIMCHI_RICE, NUTRITION_TABLE


def test_synonyms_normalize_to_representative():
    assert normalize_ingredient_name("달걀") == "계란"
    assert normalize_ingredient_name(" 쇠고기 ") == "소고기"
    assert normalize_ingredient_name("처음보는재료") == "처음보는재료"


def test_combination_warnings_pair_and_category():
    pair = combination_warnings(["꽃게", "단감", "간장"])
    assert any("감+게" in w for w in pair)
    assert any("당근+오이" in w for w in combination_warnings(["당근", "오이"]))
    assert combination_warnings(["밥", "계란"]) == []


def test_pantry_diff_normalizes_and_dedupes():
    pantry = [Ingredient(name="계란", amount=2, unit="개"), Ingredient(name="대파")]
    update = IngredientUpdate(add=[Ingredient(name="달걀", amount=5, unit="개"), Ingredient(name="김치")],
                              remove_names=["대파"])
    result = {i.name: i for i in apply_pantry_update(pantry, update)}
    assert set(result) == {"계란", "김치"}
    assert result["계란"].amount == 5


def test_pantry_replace_drops_previous():
    pantry = [Ingredient(name="계란")]
    update = IngredientUpdate(mode="replace", add=[Ingredient(name="두부")])
    assert [i.name for i in apply_pantry_update(pantry, update)] == ["두부"]


def test_missing_ingredients_uses_synonyms():
    assert missing_ingredients(KIMCHI_RICE, ["달걀", "밥", "묵은지"]) == ["참기름"]


@pytest.mark.parametrize(
    ("name", "amount", "unit", "grams"),
    [("밥", 1, "공기", 210), ("계란", 2, "개", 100), ("간장", 1, "큰술", 15), ("김치", 100, "g", 100),
     ("참기름", None, "", None), ("사과", 1, "개", None)],
)
def test_to_grams(name, amount, unit, grams):
    assert to_grams(name, amount, unit) == grams


def test_calc_nutrition_per_serving_and_unmatched():
    info = calc_nutrition(KIMCHI_RICE, InMemoryNutritionDB(NUTRITION_TABLE))
    # 밥 210g + 김치 100g + 계란 50g
    assert info.per_serving["kcal"] == round(143 * 2.1 + 18 + 136 * 0.5, 1)
    assert info.unmatched == ["참기름"]
    two = KIMCHI_RICE.model_copy(update={"servings": 2})
    assert calc_nutrition(two, InMemoryNutritionDB(NUTRITION_TABLE)).per_serving["kcal"] == round(
        info.per_serving["kcal"] / 2, 1
    )


def test_retriever_ranks_by_overlap():
    tofu = RecipeIngredient(name="두부", amount=1, unit="모")
    other = StructuredRecipe(title="두부조림", ingredients=[tofu], steps=["조린다"])
    r = InMemoryRetriever([other, KIMCHI_RICE])
    assert [x.title for x in r.search(["밥", "김치"])] == ["김치볶음밥"]
    assert r.search(["사과"]) == []
