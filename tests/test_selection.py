"""후보 선택 흐름 (초안 E1): 입력 게이트 → 결정 모델(규칙 → 작은 모델 → 되묻기)."""

import pytest

from recipe_chatbot.evalset import load_cases
from recipe_chatbot.graph import build_graph, recursion_limit
from recipe_chatbot.schemas import RecipeDraft, RecipeIngredient, StructuredRecipe
from recipe_chatbot.selection import is_selection_like, resolve

from .conftest import KIMCHI_RICE

CASES = load_cases("data/eval/v0/selection.jsonl", "selection")


# ------------------------------------------------------------------ 규칙 (평가 데이터로 검사)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_gate_matches_eval_data(case):
    assert is_selection_like(case.utterance, case.options) == case.expected.gate


@pytest.mark.parametrize("case", [c for c in CASES if c.expected.gate], ids=lambda c: c.id)
def test_rules_match_eval_data(case):
    res = resolve(case.utterance, case.options)
    if not case.expected.rules_only:
        assert res.kind == "unknown"  # 작은 모델이 맡아야 하는 케이스
    elif case.expected.final == "one":
        assert (res.kind, res.number) == ("one", case.expected.number)
    elif case.expected.final == "many":
        assert res.kind == "many"


# ------------------------------------------------------------------ 그래프 (가짜 모델)
def _recipe(title, *names):
    return StructuredRecipe(title=title, servings=1, cook_time=10,
                            ingredients=[RecipeIngredient(name=n, amount=1, unit="개") for n in names],
                            steps=["만든다"])


EGG_ROLL = _recipe("계란말이", "계란", "대파")
PORK_SOUP = _recipe("돼지고기 계란국", "돼지고기", "계란")
PEANUT_NOODLE = _recipe("땅콩 비빔국수", "땅콩", "국수")
OPTIONS_DRAFT = RecipeDraft(recipe=KIMCHI_RICE, alternatives=[EGG_ROLL, PORK_SOUP])
ING = {"mode": "diff", "add": [{"name": "밥"}, {"name": "김치"}, {"name": "계란"}], "remove_names": []}


def _turn(app, deps, query):
    config = {"configurable": {"thread_id": "t"}, "recursion_limit": recursion_limit(deps)}
    return app.invoke({"query": query}, config)


def _first_turn(make_deps, extra=None):
    responses = {
        "SupervisorDecision": [{"next": "ingredient"}, {"next": "recipe"}, {"next": "finish"}],
        "IngredientUpdate": [ING],
        "RecipeDraft": [OPTIONS_DRAFT],
    }
    for k, v in (extra or {}).items():
        responses[k] = responses.get(k, []) + v
    deps, fake = make_deps(responses)
    app = build_graph(deps)
    out = _turn(app, deps, "밥이랑 김치, 계란 있어")
    return app, deps, fake, out


def test_first_turn_shows_numbered_options(make_deps):
    _, _, _, out = _first_turn(make_deps)
    assert out["recipe"] is None
    assert [o.title for o in out["options"]] == ["김치볶음밥", "계란말이", "돼지고기 계란국"]
    assert out["answer"].splitlines()[1:] == [
        "1. 김치볶음밥 (추가로 필요한 재료: 참기름)",
        "2. 계란말이 (추가로 필요한 재료: 대파)",
        "3. 돼지고기 계란국 (추가로 필요한 재료: 돼지고기)",
    ]


def test_number_pick_skips_supervisor(make_deps):
    app, deps, fake, _ = _first_turn(make_deps)
    calls_before = len(fake.calls)
    out = _turn(app, deps, "2번")

    assert out["recipe"].title == "계란말이"
    assert out["options"] == []
    assert out["answer"].startswith("계란말이 (1인분, 10분)")
    assert fake.calls[calls_before:] == []  # 큰 모델(수퍼바이저) 호출 0회
    assert deps.router.tracer.names("node")[-4:] == ["start_turn", "decide", "validate", "respond"]


def test_abbreviation_uses_small_model(make_deps):
    app, deps, fake, _ = _first_turn(make_deps, {"OptionChoice": [{"is_selection": True, "number": 3}]})
    out = _turn(app, deps, "돼지국 줘")
    assert out["recipe"].title == "돼지고기 계란국"
    assert fake.calls[-1] == ("small-base", "OptionChoice")


def test_many_picks_ask_again_and_keep_options(make_deps):
    app, deps, _, _ = _first_turn(make_deps)
    out = _turn(app, deps, "1번이랑 2번 둘다 궁금해")
    assert out["answer"].startswith("하나만 골라주세요.")
    assert len(out["options"]) == 3


def test_not_selection_goes_back_to_supervisor(make_deps):
    app, deps, _, _ = _first_turn(make_deps, {
        "OptionChoice": [{"is_selection": False}],
        "SupervisorDecision": [{"next": "ask_user", "question": "무엇을 도와드릴까요?"}],
    })
    out = _turn(app, deps, "그거 말고 그냥")
    assert out["answer"] == "무엇을 도와드릴까요?"
    assert deps.router.tracer.names("node")[-4:] == ["start_turn", "decide", "supervisor", "respond"]


def test_rerequest_bypasses_gate(make_deps):
    app, deps, _, _ = _first_turn(make_deps, {
        "SupervisorDecision": [{"next": "recipe"}, {"next": "finish"}],
        "RecipeDraft": [RecipeDraft(recipe=PORK_SOUP)],
    })
    out = _turn(app, deps, "다른 거 없어?")
    assert out["recipe"].title == "돼지고기 계란국"
    assert "decide" not in deps.router.tracer.names("node")[-6:]


def test_supervisor_can_select_too(make_deps):
    app, deps, _, _ = _first_turn(make_deps, {"SupervisorDecision": [{"next": "select"}]})
    # 게이트 길이 한도(30자)를 넘는 발화는 수퍼바이저가 받아서 select 를 고른다
    out = _turn(app, deps, "음 이것저것 생각해봤는데 아무래도 김치볶음밥이 제일 좋을 것 같아요")
    assert out["recipe"].title == "김치볶음밥"
    expected = ["start_turn", "supervisor", "decide", "validate", "respond"]
    assert deps.router.tracer.names("node")[-5:] == expected


def test_allergen_options_are_dropped(make_deps):
    deps, _ = make_deps({
        "SupervisorDecision": [
            {"next": "recipe", "constraints": {"allergies": ["땅콩"]}},
            {"next": "finish"},
        ],
        "RecipeDraft": [RecipeDraft(recipe=PEANUT_NOODLE, alternatives=[EGG_ROLL, KIMCHI_RICE])],
    })
    app = build_graph(deps)
    out = _turn(app, deps, "땅콩 알레르기 있어. 뭐 해먹지")
    assert [o.title for o in out["options"]] == ["계란말이", "김치볶음밥"]


def test_pantry_change_clears_options(make_deps):
    app, deps, _, _ = _first_turn(make_deps, {
        "SupervisorDecision": [{"next": "ingredient"}, {"next": "ask_user", "question": "무엇을 만들까요?"}],
        "IngredientUpdate": [{"mode": "diff", "add": [{"name": "우유"}], "remove_names": []}],
    })
    out = _turn(app, deps, "우유도 있어")
    assert out["options"] == []
