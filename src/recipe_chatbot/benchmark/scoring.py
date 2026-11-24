"""역할별 채점. 평가 케이스 하나를 실제 에이전트 노드로 돌리고, 정답과 비교한다.

에이전트는 그래프에서 쓰는 노드 함수(같은 프롬프트, 같은 메시지)를 그대로 부른다.
그래서 벤치마크 점수가 곧 그래프 안에서의 동작이다.
"""

from dataclasses import dataclass, field

from .. import prompts
from ..domain.ingredient_synonyms import normalize_ingredient_name
from ..evalset import (
    IngredientCase,
    NutritionCase,
    RecipeCase,
    SelectionCase,
    SupervisorCase,
)
from ..nodes import Deps, _summary, decide, ingredient_agent, nutrition_message, recipe_agent
from ..schemas import (
    Ingredient,
    NutritionInfo,
    NutritionNote,
    StructuredRecipe,
    SupervisorDecision,
    UserConstraints,
    ValidationResult,
)
from ..selection import is_selection_like, resolve


@dataclass
class Score:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)  # 항목별 통과 여부 (해당 없는 항목은 빠짐)
    output: dict = field(default_factory=dict)  # 확인용 모델 출력 요약


def _norm(name: str) -> str:
    return normalize_ingredient_name(name.replace(" ", ""))


def _stub(title: str) -> StructuredRecipe:
    return StructuredRecipe(title=title, ingredients=[], steps=[])


def _score(checks: dict[str, bool], output: dict) -> Score:
    return Score(passed=all(checks.values()), checks=checks, output=output)


# ------------------------------------------------------------------ 수퍼바이저
def supervisor(case: SupervisorCase, deps: Deps) -> Score:
    st = case.state
    state = {
        "query": case.utterance,
        "pantry": [Ingredient(name=n) for n in st.pantry],
        "constraints": st.constraints,
        "recipe": _stub(st.recipe_title) if st.recipe_title else None,
        "nutrition": NutritionInfo(per_serving={}) if st.has_nutrition else None,
        "validation": ValidationResult(passed=False, issues=st.validation_issues)
        if st.validation_issues
        else None,
        "options": [_stub(t) for t in st.options],
        "steps": 0,
    }
    # 노드는 순서 가드로 결정을 고치므로, 모델의 원래 결정을 보려고 같은 메시지로 직접 부른다
    decision = deps.router.invoke("supervisor", SupervisorDecision, prompts.SUPERVISOR, _summary(state))
    checks = {"next": decision.next in case.expected.next_any_of}
    if case.expected.constraints is not None:
        got = decision.constraints or UserConstraints()
        want = case.expected.constraints
        checks["constraints"] = (
            {_norm(a) for a in got.allergies} == {_norm(a) for a in want.allergies}
            and got.max_kcal == want.max_kcal
            and got.min_protein_g == want.min_protein_g
        )
    return _score(
        checks,
        {"next": decision.next, "constraints": decision.constraints and decision.constraints.model_dump()},
    )


# ------------------------------------------------------------------ 재료
def ingredient(case: IngredientCase, deps: Deps) -> Score:
    state = {"query": case.utterance, "pantry": [Ingredient(name=n) for n in case.pantry]}
    pantry = ingredient_agent(state, deps)["pantry"]
    got = {_norm(i.name): i for i in pantry}
    checks = {"final_pantry": set(got) == {_norm(n) for n in case.expected.final_pantry}}
    if case.expected.amounts:
        ok = True
        for name, (amount, unit) in case.expected.amounts.items():
            item = got.get(_norm(name))
            ok = ok and item is not None and item.amount == float(amount) and (item.unit or "") == unit
        checks["amounts"] = ok
    return _score(checks, {"pantry": sorted(got)})


# ------------------------------------------------------------------ 레시피
def _uses(recipe: StructuredRecipe, name: str) -> bool:
    n = _norm(name)
    return any(n == u or n in u for u in (_norm(i.name) for i in recipe.ingredients))


def recipe(case: RecipeCase, deps: Deps) -> Score:
    state = {
        "query": case.utterance,
        "pantry": [Ingredient(name=n) for n in case.pantry],
        "constraints": case.constraints,
        "recipe": _stub(case.previous_recipe_title) if case.previous_recipe_title else None,
    }
    out = recipe_agent(state, deps)
    candidates = out["options"] or [out["recipe"]]
    main, exp = candidates[0], case.expected
    banned = [*exp.must_not_contain, *case.constraints.allergies]

    checks = {"structured": bool(main.title and main.ingredients and main.steps)}
    if banned:  # 금지 재료는 보여주는 모든 후보에서 빠져야 한다
        checks["must_not_contain"] = not any(_uses(c, b) for c in candidates for b in banned)
    if exp.min_pantry_used:
        checks["min_pantry_used"] = sum(_uses(main, p) for p in case.pantry) >= exp.min_pantry_used
    if exp.title_contains:
        want = exp.title_contains.replace(" ", "")
        checks["title_contains"] = any(want in c.title.replace(" ", "") for c in candidates)
    if exp.must_differ_from_previous and case.previous_recipe_title:
        prev = case.previous_recipe_title.replace(" ", "")
        checks["differs_from_previous"] = all(c.title.replace(" ", "") != prev for c in candidates)
    if exp.max_cook_time is not None:
        checks["max_cook_time"] = main.cook_time is not None and main.cook_time <= exp.max_cook_time
    # dish_exists(실존하지 않는 요리 요청)는 기대 동작이 아직 정해지지 않아 채점하지 않는다
    return _score(checks, {"titles": [c.title for c in candidates]})


# ------------------------------------------------------------------ 영양
def nutrition(case: NutritionCase, deps: Deps) -> Score:
    user = nutrition_message(case.recipe.title, case.nutrition, case.constraints)
    note = deps.router.invoke("nutrition", NutritionNote, prompts.NUTRITION, user)
    checks = {"substitutes": bool(note.substitutes) == case.expected.substitutes_required}
    if case.expected.must_mention:
        checks["must_mention"] = any(w in note.summary for w in case.expected.must_mention)
    return _score(checks, {"summary": note.summary, "substitutes": note.substitutes})


# ------------------------------------------------------------------ 선택 (게이트 + 결정 모델)
def selection(case: SelectionCase, deps: Deps) -> Score:
    exp = case.expected
    gate = is_selection_like(case.utterance, case.options)
    checks = {"gate": gate == exp.gate}
    if not gate:
        final, number, used_model = "supervisor", None, False
    else:
        before = len(deps.router.tracer.events)
        state = {"query": case.utterance, "options": [_stub(t) for t in case.options], "steps": 0}
        out = decide(state, deps)
        used_model = len(deps.router.tracer.events) > before
        if out.get("selection") == "chosen":
            final, number = "one", case.options.index(out["recipe"].title) + 1
        elif out.get("selection") == "not_selection":
            final, number = "supervisor", None
        else:
            final = "many" if resolve(case.utterance, case.options).kind == "many" else "ask"
            number = None
    checks["final"] = final == exp.final and number == exp.number
    if exp.gate:
        checks["rules_only"] = (not used_model) == exp.rules_only
    return _score(checks, {"gate": gate, "final": final, "number": number, "used_model": used_model})


SCORERS = {
    "supervisor": supervisor,
    "ingredient": ingredient,
    "recipe": recipe,
    "nutrition": nutrition,
    "selection": selection,
}
