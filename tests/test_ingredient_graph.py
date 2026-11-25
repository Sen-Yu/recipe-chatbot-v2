"""재료 관계 사전: 파일 형식 계약과 관계 종류별 조회 한 가지씩."""

from recipe_chatbot.domain.ingredient_graph import load_graph


def test_graph_file_is_consistent():
    g = load_graph()
    # same은 대표 이름 하나로 모여야 한다 (순환 없음)
    for e in g.edges:
        if e.relation == "same":
            assert g.canonical(e.name) != e.name, e
    # 같은 재료라고 한 쌍을 다르다고도 하면 안 된다
    for e in g.edges:
        if e.relation == "different":
            assert g.canonical(e.name) != g.canonical(e.target), e


def test_relations():
    g = load_graph()
    assert g.canonical("달걀") == "계란"
    assert g.satisfies("버섯", "표고버섯")  # 종류
    assert not g.satisfies("표고버섯", "버섯")
    assert g.satisfies("다진마늘", "마늘")  # 손질 형태
    assert g.satisfies("간마늘", "통마늘")
    assert "밀가루" in g.allergens("부침가루")  # 알레르기
    assert "우유" in g.allergens("모짜렐라치즈")
    assert "물엿" in g.substitutes("올리고당")
    assert g.are_different("배추", "양배추")
