"""그래프 전체 흐름 (가짜 모델). 시작 → 1 → A/B/C → 2 → 3 → 끝."""

from recipe_chatbot.graph import build_graph, recursion_limit
from recipe_chatbot.nodes import Settings, check
from recipe_chatbot.schemas import NutritionInfo, RecipeDraft, RecipeIngredient, UserConstraints

from .conftest import KIMCHI_RICE

NOTE = {"summary": "한 끼로 적당한 열량이에요.", "substitutes": []}
ING_UPDATE = {"mode": "diff", "add": [{"name": "밥"}, {"name": "김치"}, {"name": "달걀"}], "remove_names": []}


def run(deps, query, thread="t1"):
    app = build_graph(deps)
    config = {"configurable": {"thread_id": thread}, "recursion_limit": recursion_limit(deps)}
    return app, app.invoke({"query": query}, config)


def decisions(*steps):
    return [{"next": s} for s in steps]


def test_happy_path_start_to_end(make_deps):
    deps, fake = make_deps({
        "SupervisorDecision": decisions("ingredient", "recipe", "nutrition", "finish"),
        "IngredientUpdate": [ING_UPDATE],
        "RecipeDraft": [RecipeDraft(recipe=KIMCHI_RICE)],
        "NutritionNote": [NOTE],
    })
    _, out = run(deps, "밥이랑 김치, 달걀 있어")

    assert [i.name for i in out["pantry"]] == ["밥", "김치", "계란"]
    assert out["validation"].passed
    assert out["answer"].startswith("김치볶음밥 (1인분, 15분)")
    assert "추가로 필요한 재료: 참기름" in out["answer"]
    assert "kcal" in out["answer"]
    # 노드 순서: 번호 흐름 그대로
    assert deps.router.tracer.names("node") == [
        "start_turn", "supervisor", "ingredient_agent", "supervisor", "recipe_agent", "supervisor",
        "nutrition_agent", "supervisor", "validate", "respond",
    ]
    # 모델 라우팅: 수퍼바이저·레시피는 큰 모델 어댑터, 재료·영양은 작은 모델
    assert ("recipe-lora", "RecipeDraft") in fake.calls
    assert ("small-base", "IngredientUpdate") in fake.calls
    assert ("small-base", "NutritionNote") in fake.calls


def test_validation_failure_loops_back_then_passes(make_deps):
    with_peanut = KIMCHI_RICE.model_copy(
        update={"ingredients": [*KIMCHI_RICE.ingredients, RecipeIngredient(name="땅콩", amount=10, unit="g")]}
    )
    deps, _ = make_deps({
        "SupervisorDecision": [
            {"next": "ingredient", "constraints": {"allergies": ["땅콩"]}},
            *decisions("recipe", "finish", "recipe", "finish"),
        ],
        "IngredientUpdate": [ING_UPDATE],
        "RecipeDraft": [RecipeDraft(recipe=with_peanut), RecipeDraft(recipe=KIMCHI_RICE)],
    })
    _, out = run(deps, "밥 김치 달걀 있어. 땅콩 알레르기 있어")

    assert out["validation"].passed
    assert out["retries"] == 1
    assert "땅콩" not in out["answer"].split("재료:")[1].split("\n")[0]
    assert deps.router.tracer.names("node").count("validate") == 2


def test_gives_up_after_max_retries(make_deps):
    with_peanut = KIMCHI_RICE.model_copy(
        update={"ingredients": [*KIMCHI_RICE.ingredients, RecipeIngredient(name="땅콩", amount=10, unit="g")]}
    )
    deps, _ = make_deps(
        {
            "SupervisorDecision": [
                {"next": "recipe", "constraints": {"allergies": ["땅콩"]}},
                *decisions("finish", "recipe", "finish"),
            ],
            "RecipeDraft": [RecipeDraft(recipe=with_peanut)] * 2,
        },
        settings=Settings(max_steps=8, max_retries=1),
    )
    _, out = run(deps, "땅콩 빼고 아무거나")

    assert not out["validation"].passed
    assert out["answer"].startswith("조건을 모두 맞추지 못했어요: 알레르기 재료 포함: 땅콩")


def test_ask_user_ends_turn_with_question(make_deps):
    deps, _ = make_deps({"SupervisorDecision": [{"next": "ask_user", "question": "어떤 재료가 있나요?"}]})
    _, out = run(deps, "뭐 먹지")

    assert out["answer"] == "어떤 재료가 있나요?"
    assert deps.router.tracer.names("node") == ["start_turn", "supervisor", "respond"]


def test_step_limit_forces_finish(make_deps):
    deps, _ = make_deps(
        {"SupervisorDecision": decisions("ingredient", "ingredient"),
         "IngredientUpdate": [ING_UPDATE, ING_UPDATE]},
        settings=Settings(max_steps=2, max_retries=0),
    )
    _, out = run(deps, "밥 김치 달걀")

    assert out["answer"] == "레시피를 만들지 못했어요. 가진 재료를 알려주세요."
    assert deps.router.tracer.names("node")[-2:] == ["validate", "respond"]


def test_second_turn_keeps_pantry_and_resets_turn_counters(make_deps):
    deps, _ = make_deps({
        "SupervisorDecision": [
            {"next": "ingredient"},
            {"next": "ask_user", "question": "어떤 요리를 원하세요?"},
            *decisions("recipe", "finish"),
        ],
        "IngredientUpdate": [ING_UPDATE],
        "RecipeDraft": [RecipeDraft(recipe=KIMCHI_RICE)],
    })
    app, first = run(deps, "밥 김치 달걀 있어")
    assert first["answer"] == "어떤 요리를 원하세요?"

    config = {"configurable": {"thread_id": "t1"}, "recursion_limit": recursion_limit(deps)}
    second = app.invoke({"query": "그걸로 뭐 만들 수 있어?"}, config)
    assert [i.name for i in second["pantry"]] == ["밥", "김치", "계란"]
    assert second["steps"] == 2 and second["retries"] == 0
    assert second["validation"].passed


def test_nutrition_before_recipe_is_redirected(make_deps):
    deps, _ = make_deps({
        "SupervisorDecision": decisions("nutrition", "finish"),
        "RecipeDraft": [RecipeDraft(recipe=KIMCHI_RICE)],
    })
    _, out = run(deps, "칼로리 알려줘")
    assert "recipe_agent" in deps.router.tracer.names("node")
    assert "nutrition_agent" not in deps.router.tracer.names("node")
    assert out["validation"].passed


def test_check_nutrition_constraints():
    info = NutritionInfo(per_serving={"kcal": 500.0, "protein_g": 10.0})
    state = {"recipe": KIMCHI_RICE, "nutrition": info,
             "constraints": UserConstraints(max_kcal=400, min_protein_g=20)}
    issues = check(state).issues
    assert issues == ["칼로리 초과: 500.0 kcal > 400.0 kcal", "단백질 부족: 10.0 g < 20.0 g"]
    assert check({"recipe": KIMCHI_RICE, "constraints": UserConstraints(max_kcal=400)}).issues == [
        "영양 조건이 있는데 영양 정보가 없습니다"
    ]
