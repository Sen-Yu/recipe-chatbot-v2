"""에이전트가 쓰는 도구. 모두 LLM 없이 코드로 동작한다.

- 재료 목록 갱신, 궁합 경고 (재료 에이전트)
- 레시피 검색 (레시피 에이전트)
- 영양 계산 (영양 에이전트)
"""

from collections.abc import Iterable
from typing import Protocol

from .domain.food_combination_matcher import check_all
from .domain.ingredient_synonyms import is_ingredient_satisfied, normalize_ingredient_name
from .schemas import Ingredient, IngredientUpdate, NutritionInfo, StructuredRecipe


# ------------------------------------------------------------------ 재료
def apply_pantry_update(pantry: list[Ingredient], update: IngredientUpdate) -> list[Ingredient]:
    """보유 재료 목록에 변경을 반영한다. 재료명은 동의어 대표명으로 정규화하고 중복은 합친다."""
    current = [] if update.mode == "replace" else list(pantry)
    removed = {normalize_ingredient_name(n) for n in update.remove_names}
    current = [i for i in current if normalize_ingredient_name(i.name) not in removed]

    by_name = {normalize_ingredient_name(i.name): i for i in current}
    for item in update.add:
        key = normalize_ingredient_name(item.name)
        by_name[key] = Ingredient(name=key, amount=item.amount, unit=item.unit)
    return list(by_name.values())


def combination_warnings(names: Iterable[str]) -> list[str]:
    """궁합이 좋지 않다고 알려진 조합 경고 (v1 build_combination_warnings 와 같은 형식)."""
    result = check_all(list(names))
    warnings = []
    for item in result["pair_matches"]:
        a, b = item["pair"]
        warnings.append(f"{a}+{b}: {item['reason']} ({item['level']})")
    for item in result["category_matches"]:
        warnings.append(f"{', '.join(item['categories'])}: {item['reason']} ({item['level']})")
    return warnings


def missing_ingredients(recipe: StructuredRecipe, owned: Iterable[str]) -> list[str]:
    """레시피 재료 중 보유 재료로 충족되지 않는 것."""
    owned = list(owned)
    return [
        r.name for r in recipe.ingredients
        if not any(is_ingredient_satisfied(r.name, o) for o in owned)
    ]


# ------------------------------------------------------------------ 레시피 검색
class RecipeRetriever(Protocol):
    def search(self, ingredient_names: list[str], k: int = 3) -> list[StructuredRecipe]: ...


class InMemoryRetriever:
    """보유 재료와 많이 겹치는 순서로 고른다. 실제 RAG(벡터 검색)는 같은 인터페이스로 교체한다."""

    def __init__(self, recipes: list[StructuredRecipe] | None = None):
        self.recipes = recipes or []

    def search(self, ingredient_names: list[str], k: int = 3) -> list[StructuredRecipe]:
        scored = []
        for r in self.recipes:
            missing = missing_ingredients(r, ingredient_names)
            covered = len(r.ingredients) - len(missing)
            if covered:
                scored.append((covered / len(r.ingredients), r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:k]]


# ------------------------------------------------------------------ 영양
NUTRIENT_KEYS = ("kcal", "protein_g", "fat_g", "carb_g")

# 단위 → g (ml는 물 기준 1g으로 근사)
UNIT_GRAMS = {
    "g": 1.0, "그램": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0, "L": 1000.0,
    "큰술": 15.0, "T": 15.0, "작은술": 5.0, "t": 5.0, "컵": 200.0,
}

# (대표 재료명, 단위) → 1단위 무게(g). 단위가 '개' 같은 재료별 단위일 때 쓴다.
# 초기 세트. 영양 DB를 연동할 때 함께 늘린다.
PIECE_GRAMS = {
    ("계란", "개"): 50.0,
    ("양파", "개"): 200.0,
    ("감자", "개"): 150.0,
    ("당근", "개"): 150.0,
    ("밥", "공기"): 210.0,
    ("두부", "모"): 300.0,
    ("대파", "대"): 80.0,
}


class NutritionDB(Protocol):
    def per_100g(self, name: str) -> dict[str, float] | None:
        """대표 재료명의 100g당 영양 성분. 없으면 None."""
        ...


class InMemoryNutritionDB:
    """식약처 식품영양성분 DB를 연동하기 전까지 쓰는 표. 같은 인터페이스로 교체한다."""

    def __init__(self, table: dict[str, dict[str, float]] | None = None):
        self.table = {normalize_ingredient_name(k): v for k, v in (table or {}).items()}

    def per_100g(self, name: str) -> dict[str, float] | None:
        return self.table.get(normalize_ingredient_name(name))


def to_grams(name: str, amount: float | None, unit: str | None) -> float | None:
    """재료 양을 g으로 바꾼다. 바꿀 수 없으면 None."""
    if amount is None or amount <= 0:
        return None
    unit = (unit or "").strip()
    if unit in UNIT_GRAMS:
        return amount * UNIT_GRAMS[unit]
    piece = PIECE_GRAMS.get((normalize_ingredient_name(name), unit))
    return amount * piece if piece else None


def calc_nutrition(recipe: StructuredRecipe, db: NutritionDB) -> NutritionInfo:
    """레시피 전체 영양을 계산해 1인분으로 나눈다. 계산하지 못한 재료는 unmatched 에 남긴다."""
    total = dict.fromkeys(NUTRIENT_KEYS, 0.0)
    matched, unmatched = [], []
    for item in recipe.ingredients:
        grams = to_grams(item.name, item.amount, item.unit)
        per = db.per_100g(item.name)
        if grams is None or per is None:
            unmatched.append(item.name)
            continue
        matched.append(item.name)
        for key in NUTRIENT_KEYS:
            total[key] += per.get(key, 0.0) * grams / 100
    per_serving = {k: round(v / recipe.servings, 1) for k, v in total.items()}
    return NutritionInfo(per_serving=per_serving, matched=matched, unmatched=unmatched)
