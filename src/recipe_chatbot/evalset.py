"""역할별 평가 데이터셋 형식 (로드맵 1.1).

데이터는 data/eval/<버전>/<역할>.jsonl 에 한 줄에 한 케이스로 둔다.
이 모듈은 형식 정의와 불러오기·형식 검사만 맡는다. 채점은 1.5(평가 지표)에서 만든다.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .schemas import NutritionInfo, StructuredRecipe, UserConstraints

Role = Literal["supervisor", "ingredient", "recipe", "nutrition"]
ROLES: tuple[Role, ...] = ("supervisor", "ingredient", "recipe", "nutrition")
NextAction = Literal["ingredient", "recipe", "nutrition", "finish", "ask_user"]


class CaseBase(BaseModel):
    id: str = Field(description="역할 접두어 + 번호. 예: sup-001")
    source: str = Field(description="출처. 예: v1:기능-A3, v1:E2E-17-턴2, v1:웹검색-5, manual")
    tags: list[str] = Field(default_factory=list)
    note: str = ""


# ------------------------------------------------------------------ 수퍼바이저
class SupervisorState(BaseModel):
    pantry: list[str] = Field(default_factory=list)
    recipe_title: str | None = None
    has_nutrition: bool = False
    validation_issues: list[str] = Field(default_factory=list)
    constraints: UserConstraints = Field(default_factory=UserConstraints)


class SupervisorExpected(BaseModel):
    next_any_of: list[NextAction] = Field(min_length=1, description="정답으로 인정하는 다음 행동")
    constraints: UserConstraints | None = Field(
        default=None, description="이번 발화에서 읽어야 하는 조건. null이면 검사하지 않음"
    )


class SupervisorCase(CaseBase):
    utterance: str
    state: SupervisorState = Field(default_factory=SupervisorState)
    expected: SupervisorExpected


# ------------------------------------------------------------------ 재료
class IngredientExpected(BaseModel):
    final_pantry: list[str] = Field(description="변경 반영 후 보유 재료 (동의어 대표명 기준 집합 비교)")
    amounts: dict[str, list[float | str]] = Field(
        default_factory=dict, description="양까지 확인할 재료. 예: {'양파': [2, '개']}"
    )


class IngredientCase(CaseBase):
    utterance: str
    pantry: list[str] = Field(default_factory=list)
    expected: IngredientExpected


# ------------------------------------------------------------------ 레시피
class RecipeExpected(BaseModel):
    must_not_contain: list[str] = Field(default_factory=list, description="들어가면 안 되는 재료")
    min_pantry_used: int = Field(default=0, ge=0, description="보유 재료를 최소 몇 개 써야 하는지")
    title_contains: str | None = Field(default=None, description="요리 이름 요청일 때 제목에 들어갈 말")
    dish_exists: bool | None = Field(
        default=None, description="요리 이름 요청일 때 실존 여부. null이면 정답 미확정"
    )
    max_cook_time: int | None = None


class RecipeCase(CaseBase):
    utterance: str
    pantry: list[str] = Field(default_factory=list)
    constraints: UserConstraints = Field(default_factory=UserConstraints)
    dish_name: str | None = None
    previous_recipe_title: str | None = None
    expected: RecipeExpected


# ------------------------------------------------------------------ 영양
class NutritionExpected(BaseModel):
    must_mention: list[str] = Field(default_factory=list, description="설명에 들어가야 하는 말 (하나라도)")
    substitutes_required: bool = Field(description="대체 재료 제안이 필요한지")


class NutritionCase(CaseBase):
    recipe: StructuredRecipe
    nutrition: NutritionInfo
    constraints: UserConstraints = Field(default_factory=UserConstraints)
    expected: NutritionExpected

    @model_validator(mode="after")
    def _unmatched_in_recipe(self):
        names = {i.name for i in self.recipe.ingredients}
        missing = [u for u in self.nutrition.unmatched if u not in names]
        if missing:
            raise ValueError(f"unmatched 재료가 레시피에 없습니다: {missing}")
        return self


CASE_TYPES: dict[Role, type[CaseBase]] = {
    "supervisor": SupervisorCase,
    "ingredient": IngredientCase,
    "recipe": RecipeCase,
    "nutrition": NutritionCase,
}
ID_PREFIX: dict[Role, str] = {"supervisor": "sup", "ingredient": "ing", "recipe": "rec", "nutrition": "nut"}


def load_cases(path: str | Path, role: Role) -> list[CaseBase]:
    """JSONL 을 읽어 역할에 맞는 케이스 목록으로 돌려준다. 형식이 틀리면 줄 번호와 함께 오류."""
    case_type = CASE_TYPES[role]
    cases = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(case_type.model_validate(json.loads(line)))
        except Exception as e:
            raise ValueError(f"{path}:{n}: {e}") from e
    return cases


def check_dataset(root: str | Path) -> dict[Role, int]:
    """한 버전 폴더의 네 파일을 모두 읽고, id 형식과 중복을 검사한다. 역할별 케이스 수를 돌려준다."""
    counts: dict[Role, int] = {}
    seen: set[str] = set()
    for role in ROLES:
        cases = load_cases(Path(root) / f"{role}.jsonl", role)
        for c in cases:
            if not c.id.startswith(ID_PREFIX[role] + "-"):
                raise ValueError(f"{role}: id '{c.id}'는 '{ID_PREFIX[role]}-'로 시작해야 합니다")
            if c.id in seen:
                raise ValueError(f"중복 id: {c.id}")
            seen.add(c.id)
        counts[role] = len(cases)
    return counts
