"""에이전트 사이의 데이터 계약.

모델이 구조화 출력으로 만드는 스키마(IngredientUpdate, RecipeDraft, NutritionNote, SupervisorDecision)와
코드가 만드는 스키마(NutritionInfo, ValidationResult)를 함께 둔다.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    """사용자가 가진 재료 한 가지."""

    name: str = Field(description="재료명. 예: 계란, 밥, 김치")
    amount: float | None = Field(default=None, description="양. 모르면 null")
    unit: str | None = Field(default=None, description="단위. 예: 개, g, 큰술, 공기")


class IngredientUpdate(BaseModel):
    """이번 발화가 보유 재료 목록에 가하는 변경 (재료 에이전트 출력)."""

    mode: Literal["diff", "replace"] = Field(
        default="diff", description="diff: add/remove만 반영, replace: 목록을 add로 통째로 교체"
    )
    add: list[Ingredient] = Field(default_factory=list, description="추가하거나 새로 언급된 재료")
    remove_names: list[str] = Field(default_factory=list, description="목록에서 뺄 재료명")


class RecipeIngredient(BaseModel):
    """레시피에 들어가는 재료. amount가 null이면 '약간'처럼 수치가 없는 경우."""

    name: str
    amount: float | None = None
    unit: str = ""


class StructuredRecipe(BaseModel):
    recipe_id: str | None = Field(default=None, description="RAG 출처 id. 생성 레시피는 null")
    title: str
    servings: int = Field(default=1, ge=1)
    cook_time: int | None = Field(default=None, description="조리 시간(분)")
    ingredients: list[RecipeIngredient]
    steps: list[str]


class RecipeDraft(BaseModel):
    """레시피 에이전트 출력."""

    recipe: StructuredRecipe


class UserConstraints(BaseModel):
    """사용자 조건. 수퍼바이저가 발화에서 읽어 누적한다."""

    allergies: list[str] = Field(default_factory=list, description="먹으면 안 되는 재료")
    max_kcal: float | None = Field(default=None, description="1인분 최대 칼로리")
    min_protein_g: float | None = Field(default=None, description="1인분 최소 단백질(g)")


class NutritionInfo(BaseModel):
    """영양 계산 결과 (코드가 만든다). 값은 1인분 기준."""

    per_serving: dict[str, float]
    matched: list[str] = Field(default_factory=list, description="계산에 들어간 재료")
    unmatched: list[str] = Field(default_factory=list, description="DB나 단위를 못 찾아 빠진 재료")


class NutritionNote(BaseModel):
    """영양 에이전트 출력: 계산 결과에 대한 짧은 설명과 대체 재료."""

    summary: str
    substitutes: list[str] = Field(default_factory=list)


class SupervisorDecision(BaseModel):
    """수퍼바이저 출력: 다음에 할 일."""

    next: Literal["ingredient", "recipe", "nutrition", "finish", "ask_user"]
    reason: str = ""
    question: str | None = Field(default=None, description="ask_user일 때 사용자에게 되물을 문장")
    constraints: UserConstraints | None = Field(
        default=None, description="이번 발화에서 새로 읽은 조건. 없으면 null"
    )


class ValidationResult(BaseModel):
    """검증 노드 결과 (코드가 만든다)."""

    passed: bool
    issues: list[str] = Field(default_factory=list)
