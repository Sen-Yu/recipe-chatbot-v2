"""벤치마크 실행기. 실제 모델 없이 채점 로직과 평가 데이터가 서로 맞는지 확인한다."""

from recipe_chatbot.benchmark.runner import run, to_markdown
from recipe_chatbot.evalset import ROLES, load_cases
from recipe_chatbot.models import FakeChatClient, ModelRouter
from recipe_chatbot.schemas import (
    Ingredient,
    IngredientUpdate,
    NutritionNote,
    OptionChoice,
    RecipeDraft,
    RecipeIngredient,
    StructuredRecipe,
    SupervisorDecision,
)
from recipe_chatbot.selection import is_selection_like, resolve

from .conftest import SPECS

DATA = "data/eval/v0"


def oracle_answers() -> dict[str, list]:
    """케이스마다 정답대로 답하는 가짜 모델의 응답 목록 (역할 순서대로)."""
    out: dict[str, list] = {}
    add = lambda schema, item: out.setdefault(schema, []).append(item)  # noqa: E731
    for role in ROLES:
        for c in load_cases(f"{DATA}/{role}.jsonl", role):
            e = c.expected
            if role == "supervisor":
                add(
                    "SupervisorDecision", SupervisorDecision(next=e.next_any_of[0], constraints=e.constraints)
                )
            elif role == "ingredient":
                items = [Ingredient(name=n) for n in e.final_pantry]
                for i in items:
                    if i.name in e.amounts:
                        i.amount, i.unit = float(e.amounts[i.name][0]), e.amounts[i.name][1]
                add("IngredientUpdate", IngredientUpdate(mode="replace", add=items))
            elif role == "recipe":
                banned = {*e.must_not_contain, *c.constraints.allergies}
                used = [p for p in c.pantry if p not in banned] or ["물"]
                title = e.title_contains or c.dish_name or "테스트 요리"
                recipe = StructuredRecipe(
                    title=title,
                    cook_time=e.max_cook_time or 20,
                    ingredients=[RecipeIngredient(name=n) for n in used],
                    steps=["손질한다", "익힌다"],
                )
                add("RecipeDraft", RecipeDraft(recipe=recipe))
            elif role == "nutrition":
                summary = e.must_mention[0] if e.must_mention else "괜찮아요"
                add(
                    "NutritionNote",
                    NutritionNote(summary=summary, substitutes=["두부"] if e.substitutes_required else []),
                )
            elif role == "selection":
                if (
                    is_selection_like(c.utterance, c.options)
                    and resolve(c.utterance, c.options).kind == "unknown"
                ):
                    add("OptionChoice", OptionChoice(is_selection=e.final != "supervisor", number=e.number))
    return out


def test_oracle_scores_everything_and_report_renders():
    fake = FakeChatClient(oracle_answers())
    report = run(ModelRouter(SPECS, {"big": fake, "small": fake}), DATA)
    failed = [(c["id"], c["checks"], c["error"]) for c in report["cases"] if not c["passed"]]
    assert failed == []
    assert {s["pass_rate"] for s in report["summary"].values()} == {1.0}
    assert "| supervisor |" in to_markdown(report)


def test_broken_output_is_counted_not_raised():
    fake = FakeChatClient({"SupervisorDecision": ["{not json"] * 6})
    report = run(ModelRouter(SPECS, {"big": fake, "small": fake}), DATA, roles=["supervisor"], limit=3)
    s = report["summary"]["supervisor"]
    assert (s["cases"], s["pass_rate"], s["structured_ok_rate"], s["first_try_rate"]) == (3, 0.0, 0.0, 0.0)
    assert all("StructuredOutputError" in c["error"] for c in report["cases"])
