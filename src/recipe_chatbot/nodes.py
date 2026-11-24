"""그래프 노드. 번호는 docs/diagrams/architecture-v2.spec.json 과 같다.

1 supervisor · A ingredient_agent · B recipe_agent · C nutrition_agent · 2 validate · 3 respond
"""

import json
import os
from dataclasses import dataclass, field

from . import prompts
from .domain.ingredient_synonyms import normalize_ingredient_name
from .images import ImageGenerator, NullImageGenerator
from .models import ModelRouter
from .schemas import (
    IngredientUpdate,
    NutritionNote,
    RecipeDraft,
    SupervisorDecision,
    UserConstraints,
    ValidationResult,
)
from .state import GraphState
from .tools import (
    InMemoryNutritionDB,
    InMemoryRetriever,
    NutritionDB,
    RecipeRetriever,
    apply_pantry_update,
    calc_nutrition,
    combination_warnings,
    missing_ingredients,
)


@dataclass
class Settings:
    max_steps: int = 8  # 한 턴에 수퍼바이저가 결정할 수 있는 최대 횟수
    max_retries: int = 2  # 검증 미달 시 되돌아가는 최대 횟수

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            max_steps=int(os.environ.get("RECIPE_MAX_STEPS", cls.max_steps)),
            max_retries=int(os.environ.get("RECIPE_MAX_RETRIES", cls.max_retries)),
        )


@dataclass
class Deps:
    router: ModelRouter
    retriever: RecipeRetriever = field(default_factory=InMemoryRetriever)
    nutrition_db: NutritionDB = field(default_factory=InMemoryNutritionDB)
    images: ImageGenerator = field(default_factory=NullImageGenerator)
    settings: Settings = field(default_factory=Settings)


# ------------------------------------------------------------------ 턴 시작
def start_turn(state: GraphState) -> GraphState:
    """턴마다 초기화되는 값을 비운다. 대화 내내 유지되는 값은 기본값만 채운다."""
    return {
        "steps": 0,
        "retries": 0,
        "decision": None,
        "validation": None,
        "answer": "",
        "pantry": state.get("pantry") or [],
        "constraints": state.get("constraints") or UserConstraints(),
        "warnings": state.get("warnings") or [],
    }


# ------------------------------------------------------------------ 1 수퍼바이저
def _summary(state: GraphState) -> str:
    recipe = state.get("recipe")
    validation = state.get("validation")
    data = {
        "사용자 발화": state.get("query", ""),
        "보유 재료": [i.name for i in state.get("pantry", [])],
        "조건": state.get("constraints", UserConstraints()).model_dump(),
        "레시피": recipe.title if recipe else None,
        "영양 정보": bool(state.get("nutrition")),
        "검증 미달 사유": validation.issues if validation and not validation.passed else [],
        "이번 턴 결정 횟수": state.get("steps", 0),
    }
    return json.dumps(data, ensure_ascii=False)


def _merge_constraints(old: UserConstraints, new: UserConstraints | None) -> UserConstraints:
    if new is None:
        return old
    allergies = list(dict.fromkeys([*old.allergies, *new.allergies]))
    return UserConstraints(
        allergies=allergies,
        max_kcal=new.max_kcal if new.max_kcal is not None else old.max_kcal,
        min_protein_g=new.min_protein_g if new.min_protein_g is not None else old.min_protein_g,
    )


def supervisor(state: GraphState, deps: Deps) -> GraphState:
    steps = state.get("steps", 0)
    if steps >= deps.settings.max_steps:
        decision = SupervisorDecision(next="finish", reason="이번 턴 결정 한도에 도달")
    else:
        decision = deps.router.invoke("supervisor", SupervisorDecision, prompts.SUPERVISOR, _summary(state))

    # 순서 가드: 레시피 없이 영양 계산을 할 수 없다
    if decision.next == "nutrition" and not state.get("recipe"):
        reason = f"{decision.reason} (레시피 먼저)"
        decision = decision.model_copy(update={"next": "recipe", "reason": reason})

    return {
        "decision": decision,
        "steps": steps + 1,
        "constraints": _merge_constraints(state.get("constraints", UserConstraints()), decision.constraints),
    }


def route_supervisor(state: GraphState) -> str:
    nxt = state["decision"].next
    return {
        "ingredient": "ingredient_agent",
        "recipe": "recipe_agent",
        "nutrition": "nutrition_agent",
        "finish": "validate",
        "ask_user": "respond",
    }[nxt]


# ------------------------------------------------------------------ A 재료 에이전트
def ingredient_agent(state: GraphState, deps: Deps) -> GraphState:
    current = ", ".join(i.name for i in state.get("pantry", [])) or "(없음)"
    user = f"현재 보유 재료: {current}\n사용자 발화: {state.get('query', '')}"
    update = deps.router.invoke("ingredient", IngredientUpdate, prompts.INGREDIENT, user)
    pantry = apply_pantry_update(state.get("pantry", []), update)
    return {"pantry": pantry, "warnings": combination_warnings(i.name for i in pantry)}


# ------------------------------------------------------------------ B 레시피 에이전트
def recipe_agent(state: GraphState, deps: Deps) -> GraphState:
    names = [i.name for i in state.get("pantry", [])]
    candidates = deps.retriever.search(names, k=3)
    validation = state.get("validation")
    payload = {
        "사용자 발화": state.get("query", ""),
        "보유 재료": names,
        "조건": state.get("constraints", UserConstraints()).model_dump(),
        "검색 후보": [c.model_dump(mode="json") for c in candidates],
        "이전 레시피": state["recipe"].model_dump(mode="json") if state.get("recipe") else None,
        "검증 미달 사유": validation.issues if validation and not validation.passed else [],
    }
    draft = deps.router.invoke(
        "recipe", RecipeDraft, prompts.RECIPE, json.dumps(payload, ensure_ascii=False)
    )
    # 레시피가 바뀌면 이전 영양 정보는 맞지 않으므로 비운다
    return {"recipe": draft.recipe, "candidates": candidates, "nutrition": None, "nutrition_note": None}


# ------------------------------------------------------------------ C 영양 에이전트
def nutrition_agent(state: GraphState, deps: Deps) -> GraphState:
    info = calc_nutrition(state["recipe"], deps.nutrition_db)
    payload = {
        "레시피": state["recipe"].title,
        "1인분 영양": info.per_serving,
        "계산에서 빠진 재료": info.unmatched,
        "조건": state.get("constraints", UserConstraints()).model_dump(),
    }
    note = deps.router.invoke(
        "nutrition", NutritionNote, prompts.NUTRITION, json.dumps(payload, ensure_ascii=False)
    )
    return {"nutrition": info, "nutrition_note": note}


# ------------------------------------------------------------------ 2 검증 노드 (코드)
def check(state: GraphState) -> ValidationResult:
    recipe = state.get("recipe")
    constraints = state.get("constraints", UserConstraints())
    nutrition = state.get("nutrition")
    issues = []

    if recipe is None:
        return ValidationResult(passed=False, issues=["레시피가 없습니다"])

    used = {normalize_ingredient_name(i.name) for i in recipe.ingredients}
    for allergen in constraints.allergies:
        a = normalize_ingredient_name(allergen)
        if any(a == u or a in u for u in used):
            issues.append(f"알레르기 재료 포함: {allergen}")

    needs_nutrition = constraints.max_kcal is not None or constraints.min_protein_g is not None
    if needs_nutrition and nutrition is None:
        issues.append("영양 조건이 있는데 영양 정보가 없습니다")
    if nutrition is not None:
        kcal = nutrition.per_serving.get("kcal", 0)
        protein = nutrition.per_serving.get("protein_g", 0)
        if constraints.max_kcal is not None and kcal > constraints.max_kcal:
            issues.append(f"칼로리 초과: {kcal} kcal > {constraints.max_kcal} kcal")
        if constraints.min_protein_g is not None and protein < constraints.min_protein_g:
            issues.append(f"단백질 부족: {protein} g < {constraints.min_protein_g} g")

    return ValidationResult(passed=not issues, issues=issues)


def validate(state: GraphState, deps: Deps) -> GraphState:
    result = check(state)
    update: GraphState = {"validation": result}
    if not result.passed:
        update["retries"] = state.get("retries", 0) + 1
    return update


def route_validate(state: GraphState, max_retries: int) -> str:
    v = state["validation"]
    if v.passed or state.get("retries", 0) > max_retries:
        return "respond"
    return "supervisor"


# ------------------------------------------------------------------ 3 응답 (코드)
def _format_amount(amount: float | None, unit: str) -> str:
    if amount is None:
        return "약간"
    num = int(amount) if float(amount).is_integer() else amount
    return f"{num}{unit}"


def respond(state: GraphState, deps: Deps) -> GraphState:
    decision = state.get("decision")
    if decision is not None and decision.next == "ask_user":
        return {"answer": decision.question or "조금 더 자세히 알려주시겠어요?"}

    recipe = state.get("recipe")
    if recipe is None:
        return {"answer": "레시피를 만들지 못했어요. 가진 재료를 알려주세요."}

    lines = []
    validation = state.get("validation")
    if validation is not None and not validation.passed:
        lines.append("조건을 모두 맞추지 못했어요: " + "; ".join(validation.issues))
        lines.append("")

    head = f"{recipe.title} ({recipe.servings}인분"
    head += f", {recipe.cook_time}분)" if recipe.cook_time else ")"
    lines.append(head)
    amounts = (f"{i.name} {_format_amount(i.amount, i.unit)}" for i in recipe.ingredients)
    lines.append("재료: " + ", ".join(amounts))
    missing = missing_ingredients(recipe, [i.name for i in state.get("pantry", [])])
    if missing:
        lines.append("추가로 필요한 재료: " + ", ".join(missing))
    lines.append("조리 순서:")
    lines.extend(f"{n}. {s}" for n, s in enumerate(recipe.steps, start=1))

    nutrition = state.get("nutrition")
    if nutrition is not None:
        p = nutrition.per_serving
        text = (f"영양(1인분): 약 {p.get('kcal', 0)} kcal, 단백질 {p.get('protein_g', 0)} g, "
                f"지방 {p.get('fat_g', 0)} g, 탄수화물 {p.get('carb_g', 0)} g")
        if nutrition.unmatched:
            text += f" (계산 제외: {', '.join(nutrition.unmatched)})"
        lines.append(text)
        note = state.get("nutrition_note")
        if note is not None and note.summary:
            lines.append(note.summary)

    for w in state.get("warnings", []):
        lines.append(f"주의: {w}")
    return {"answer": "\n".join(lines)}
