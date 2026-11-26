"""재료 관계 사전 (`data/ingredients/graph.tsv`) 읽기와 조회.

관계 종류와 쓰임:
- same: 대표 이름으로 바꾼다 (`canonical`)
- form, kind: 보유 재료로 레시피 재료를 채울 수 있는지 (`satisfies`)
- contains: 알레르기 판단 (`allergens`)
- substitute: 대체 재료 추천 (`substitutes`). 알레르기 판단에는 쓰지 않는다
- different: 잘못 합치지 않도록 남긴 쌍 (`are_different`)

1차 시험 단계라 아직 그래프의 다른 곳에 연결하지 않았다 (L.6).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

GRAPH_PATH = Path(__file__).resolve().parents[3] / "data" / "ingredients" / "graph.tsv"
RELATIONS = ("same", "form", "kind", "contains", "substitute", "different")


@dataclass(frozen=True)
class Edge:
    name: str
    relation: str
    target: str
    confirmed: bool
    note: str = ""


@dataclass
class IngredientGraph:
    edges: list[Edge]
    _same: dict[str, str] = field(default_factory=dict)
    _up: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # form·kind: 이름 → 대상
    _kind_up: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # kind만
    _contains: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _subs: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _different: set[frozenset[str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        for e in self.edges:
            if e.relation == "same":
                self._same[e.name] = e.target
        for e in self.edges:
            a, b = self.canonical(e.name), self.canonical(e.target)
            if e.relation in ("form", "kind"):
                self._up[a].add(b)
            if e.relation == "kind":
                self._kind_up[a].add(b)
            elif e.relation == "contains":
                self._contains[a].add(b)
            elif e.relation == "substitute":
                self._subs[a].add(b)
                self._subs[b].add(a)
            elif e.relation == "different":
                self._different.add(frozenset((a, b)))

    def canonical(self, name: str) -> str:
        name = name.replace(" ", "")
        seen = set()
        while name in self._same and name not in seen:
            seen.add(name)
            name = self._same[name]
        return name

    def ancestors(self, name: str, kind_only: bool = False) -> set[str]:
        """form·kind(kind_only면 kind만)를 따라 올라간 이름들 (자기 자신 제외)."""
        up_map = self._kind_up if kind_only else self._up
        out: set[str] = set()
        stack = [self.canonical(name)]
        while stack:
            for up in up_map.get(stack.pop(), ()):
                if up not in out:
                    out.add(up)
                    stack.append(up)
        return out

    def root(self, name: str) -> str:
        """kind·form을 따라 맨 위 대표 이름 (상위가 둘 이상이면 거기서 멈춤). 방향 없는 같은 재료 비교용."""
        name = self.canonical(name)
        seen = {name}
        while len(ups := self._up.get(name, ())) == 1:
            (name,) = ups
            if name in seen:
                break
            seen.add(name)
        return name

    def satisfies(self, required: str, owned: str) -> bool:
        """보유 재료(owned)로 레시피 재료(required)를 채울 수 있나.

        - 같은 재료이거나
        - 보유 재료가 요구 재료의 한 종류이거나 ('표고버섯'으로 '버섯'을 채움)
        - 요구 재료가 보유 재료를 손질한 형태 ('마늘'로 '다진마늘'을 채움)

        손질 형태로 원재료를 채우지는 않는다 ('후추'(간 것)로 '통후추'를 못 채움).
        """
        req, own = self.canonical(required), self.canonical(owned)
        if req == own or req in self.ancestors(own, kind_only=True):
            return True
        # 원재료의 한 종류로도 손질 형태를 채운다 ('대파'로 '다진파'를 채움)
        kinds = {own} | self.ancestors(own, kind_only=True)
        return bool(kinds & self._form_sources(req))

    def _form_sources(self, name: str) -> set[str]:
        out: set[str] = set()
        for e in self.edges:
            if e.relation == "form" and self.canonical(e.name) == name:
                target = self.canonical(e.target)
                out |= {target} | self._form_sources(target)
        return out

    def allergens(self, name: str) -> set[str]:
        """이 재료에 들어 있는 원료 (contains, 그리고 form·kind로 올라간 재료의 contains 포함)."""
        names = {self.canonical(name)} | self.ancestors(name)
        out = set(names)
        stack = list(names)
        while stack:
            for c in self._contains.get(stack.pop(), ()):
                for x in {c} | self.ancestors(c):
                    if x not in out:
                        out.add(x)
                        stack.append(x)
        return out - {self.canonical(name)}

    def substitutes(self, name: str) -> set[str]:
        return set(self._subs.get(self.canonical(name), ()))

    def are_different(self, a: str, b: str) -> bool:
        return frozenset((self.canonical(a), self.canonical(b))) in self._different

    def known(self) -> set[str]:
        return {e.name for e in self.edges} | {e.target for e in self.edges}


def parse(text: str) -> IngredientGraph:
    edges = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 4 or cols[1] not in RELATIONS or cols[3] not in ("ok", "check"):
            raise ValueError(f"graph.tsv {lineno}번째 줄 형식이 다릅니다: {line!r}")
        note = cols[4] if len(cols) > 4 else ""
        edges.append(Edge(cols[0], cols[1], cols[2], cols[3] == "ok", note))
    return IngredientGraph(edges)


@cache
def load_graph(path: Path = GRAPH_PATH) -> IngredientGraph:
    return parse(path.read_text(encoding="utf-8"))
