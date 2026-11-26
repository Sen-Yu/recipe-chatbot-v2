"""
재료명 정규화와 보유 재료 판단.
모든 재료명 비교 지점(중복 판단, 제거 판단, 부족 재료 계산)이
이 모듈의 함수를 공유한다 — 지점마다 다르게 구현하지 말 것.

이름과 관계는 재료 관계 사전(`data/ingredients/graph.tsv`, `ingredient_graph.py`)
한 곳에만 적는다. 이 모듈은 그 사전을 읽어 예전과 같은 함수를 제공한다.
빠진 이름이 나오면 graph.tsv에 추가한다.
"""

from .ingredient_graph import load_graph

## 편의점·동네마트·이마트·홈플러스·코스트코·탑마트 6개 매장 기준으로
## 거의 취급하지 않는 특수 식재료 목록.
## RAG 레시피 적정성 평가에서 게이트(gate)로 사용됨:
## 이 목록에 있는 재료가 레시피에 하나라도 포함되면 needed_ingredients 비율과
## 무관하게 해당 레시피는 무조건 "부적합" 처리한다.
SPECIALTY_INGREDIENTS = {
    # 고급 해산물류
    "랍스터", "캐비어", "우니", "참치 대뱃살", "상어지느러미", "상어고기",
    "갯장어", "아귀간", "개불", "군소",
 
    # 특수 육류/부속류
    "우설", "우족", "안창살", "토시살", "살치살", "와규", "푸아그라",
    "사슴고기", "멧돼지고기", "거위고기", "메추리고기", "꿩고기",
    "토끼고기", "말고기", "캥거루고기", "타조고기", "악어고기", "들소고기",
 
    # 고급 버섯/식물성 재료
    "송로버섯", "송이버섯", "능이버섯", "산삼", "트레비소", "라디치오",
    "청각", "우뭇가사리",
 
    # 수입 치즈
    "그뤼에르치즈", "로크포르치즈", "만체고치즈",
 
    # 고급 향신료/조미료
    "사프란", "아사프에티다", "자타르", "하리사", "훈제파프리카가루",
    "삼발소스", "타마린드페이스트",
 
    # 주류
    "포트와인", "마데이라와인",
 
    # 기타 특수 식재료
    "자라", "개구리다리", "달팽이", "제비집", "생벌집", "카사바가루",
    "얌빈", "카다이프", "페이조아",
 
    # 식용곤충
    "밀웜", "귀뚜라미가루", "메뚜기",
}



def normalize_ingredient_name(name: str) -> str:
    """대표 이름으로 정규화 (사전에 없는 이름은 공백만 정리해 그대로 반환)."""
    return load_graph().canonical(name.strip())


def is_ingredient_satisfied(required: str, owned: str) -> bool:
    """
    레시피가 요구하는 재료(required)를 사용자가 가진 재료(owned)로 충족할 수 있는지.
    같은 재료, 하위 종류(표고버섯으로 버섯), 원재료로 손질 형태(마늘로 다진마늘)까지 반영.
    """
    return load_graph().satisfies(required.strip(), owned.strip())


def broader_names(name: str) -> set[str]:
    """종류·손질 형태를 따라 올라간 대표 이름들 (자기 자신 포함). 성분 분류용."""
    g = load_graph()
    return {g.canonical(name.strip())} | g.ancestors(name.strip())


def ingredient_contents(name: str) -> set[str]:
    """이 재료와 이 재료에 든 원료의 대표 이름들 (자기 자신 포함). 알레르기 검사용.
    예: '치킨' → {'치킨', '닭고기'}, '부침가루' → {'부침가루', '밀가루'}"""
    g = load_graph()
    return {g.canonical(name.strip())} | g.allergens(name.strip())


def root_name(name: str) -> str:
    """종류·손질 형태를 따라 맨 위 대표 이름 ('묵은지' → '김치', '다진마늘' → '마늘').
    방향 없는 같은 재료 비교(궁합 매칭)용."""
    return load_graph().root(name.strip())
