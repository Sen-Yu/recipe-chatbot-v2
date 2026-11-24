"""그래프 조립.

시작 → start_turn → 1 supervisor ⇄ (A ingredient · B recipe · C nutrition)
                         → 2 validate → (미달이면 1로) → 3 respond → 끝
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from . import nodes
from .nodes import Deps
from .state import STATE_MODELS, GraphState


def _traced(name: str, fn, deps: Deps):
    def run(state: GraphState) -> GraphState:
        with deps.router.tracer.span("node", name):
            return fn(state)

    return run


def build_graph(deps: Deps, checkpointer=None):
    g = StateGraph(GraphState)

    def add(name, fn):
        g.add_node(name, _traced(name, fn, deps))

    add("start_turn", nodes.start_turn)
    add("supervisor", lambda s: nodes.supervisor(s, deps))
    add("ingredient_agent", lambda s: nodes.ingredient_agent(s, deps))
    add("recipe_agent", lambda s: nodes.recipe_agent(s, deps))
    add("nutrition_agent", lambda s: nodes.nutrition_agent(s, deps))
    add("validate", lambda s: nodes.validate(s, deps))
    add("respond", lambda s: nodes.respond(s, deps))

    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "supervisor")
    g.add_conditional_edges(
        "supervisor",
        nodes.route_supervisor,
        ["ingredient_agent", "recipe_agent", "nutrition_agent", "validate", "respond"],
    )
    for agent in ("ingredient_agent", "recipe_agent", "nutrition_agent"):
        g.add_edge(agent, "supervisor")
    g.add_conditional_edges(
        "validate",
        lambda s: nodes.route_validate(s, deps.settings.max_retries),
        ["supervisor", "respond"],
    )
    g.add_edge("respond", END)

    if checkpointer is None:
        checkpointer = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=STATE_MODELS))
    return g.compile(checkpointer=checkpointer)


def recursion_limit(deps: Deps) -> int:
    """LangGraph 재귀 한도: 수퍼바이저 결정마다 노드 2개 + 검증·응답 여유분."""
    s = deps.settings
    return (s.max_steps + 2) * 2 * (s.max_retries + 2) + 10
