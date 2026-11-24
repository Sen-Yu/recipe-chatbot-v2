import pytest

from recipe_chatbot.models import FakeChatClient, ModelRouter, ModelSpec
from recipe_chatbot.nodes import Deps, Settings
from recipe_chatbot.schemas import RecipeIngredient, StructuredRecipe
from recipe_chatbot.tools import InMemoryNutritionDB, InMemoryRetriever

# 설계 문서 4.3: 큰 모델(수퍼바이저·레시피 어댑터) + 작은 모델(재료·영양)
SPECS = {
    "supervisor": ModelSpec(endpoint="big", base="big-base", adapter="supervisor-lora"),
    "recipe": ModelSpec(endpoint="big", base="big-base", adapter="recipe-lora"),
    "ingredient": ModelSpec(endpoint="small", base="small-base"),
    "nutrition": ModelSpec(endpoint="small", base="small-base"),
}

KIMCHI_RICE = StructuredRecipe(
    recipe_id="1055389",
    title="김치볶음밥",
    servings=1,
    cook_time=15,
    ingredients=[
        RecipeIngredient(name="밥", amount=1, unit="공기"),
        RecipeIngredient(name="김치", amount=100, unit="g"),
        RecipeIngredient(name="계란", amount=1, unit="개"),
        RecipeIngredient(name="참기름", amount=None, unit=""),
    ],
    steps=["김치를 볶는다", "밥을 넣고 함께 볶는다", "계란 프라이를 올린다"],
)

NUTRITION_TABLE = {
    "밥": {"kcal": 143, "protein_g": 2.5, "fat_g": 0.3, "carb_g": 31.7},
    "김치": {"kcal": 18, "protein_g": 1.4, "fat_g": 0.4, "carb_g": 3.0},
    "계란": {"kcal": 136, "protein_g": 12.4, "fat_g": 9.4, "carb_g": 0.3},
    "땅콩": {"kcal": 567, "protein_g": 25.8, "fat_g": 49.2, "carb_g": 16.1},
}


@pytest.fixture
def make_deps():
    def _make(responses: dict, settings: Settings | None = None, recipes=None):
        big, small = FakeChatClient(responses), None
        small = big  # 같은 가짜 클라이언트를 두 엔드포인트에 연결해서 호출 순서를 한 곳에 기록
        router = ModelRouter(SPECS, {"big": big, "small": small})
        deps = Deps(
            router=router,
            retriever=InMemoryRetriever(recipes if recipes is not None else [KIMCHI_RICE]),
            nutrition_db=InMemoryNutritionDB(NUTRITION_TABLE),
            settings=settings or Settings(),
        )
        return deps, big

    return _make
