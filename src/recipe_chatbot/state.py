"""그래프 상태.

대화가 이어져도 남는 값(pantry, constraints, recipe 등)과 턴마다 초기화되는 값(steps, retries,
decision, validation, answer)을 함께 둔다. 턴 초기화는 graph.start_turn 노드가 맡는다.
"""

from typing import TypedDict

from .schemas import (
    Ingredient,
    NutritionInfo,
    NutritionNote,
    StructuredRecipe,
    SupervisorDecision,
    UserConstraints,
    ValidationResult,
)


class GraphState(TypedDict, total=False):
    # 입력
    query: str

    # 대화 내내 유지
    pantry: list[Ingredient]
    constraints: UserConstraints
    recipe: StructuredRecipe | None
    options: list[StructuredRecipe]  # 사용자에게 보여준 후보. 고르거나 재료가 바뀌면 비운다
    candidates: list[StructuredRecipe]  # 검색 결과 (레시피 에이전트 참고용)
    nutrition: NutritionInfo | None
    nutrition_note: NutritionNote | None
    warnings: list[str]

    # 턴마다 초기화
    decision: SupervisorDecision | None
    steps: int  # 이번 턴 수퍼바이저 결정 횟수
    retries: int  # 이번 턴 검증 미달로 되돌아간 횟수
    validation: ValidationResult | None
    selection: str | None  # 결정 모델 결과: chosen | ask | not_selection
    answer: str


# 체크포인터가 상태를 저장·복원할 때 허용할 스키마 (langgraph JsonPlusSerializer)
STATE_MODELS = [
    ("recipe_chatbot.schemas", name)
    for name in (
        "Ingredient",
        "RecipeIngredient",
        "StructuredRecipe",
        "UserConstraints",
        "NutritionInfo",
        "NutritionNote",
        "SupervisorDecision",
        "ValidationResult",
    )
]
