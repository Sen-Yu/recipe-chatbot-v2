"""모델 라우터: 어댑터 선택, 재시도, OpenAI 호환 클라이언트."""

import json

import httpx
import pytest

from recipe_chatbot.models import (
    FakeChatClient,
    ModelRouter,
    ModelSpec,
    OpenAICompatibleClient,
    StructuredOutputError,
    load_router,
)
from recipe_chatbot.schemas import SupervisorDecision

from .conftest import SPECS


def test_model_specs():
    # 요청에 쓰는 모델 이름: served_name > adapter > base
    assert ModelSpec("big", "base").model_name == "base"
    assert ModelSpec("big", "base", adapter="lora").model_name == "lora"
    ollama = ModelSpec("big", "base", adapter="lora", served_name="base-lora:latest")
    assert ollama.model_name == "base-lora:latest"
    # 에이전트 하나라도 모델이 빠지면 시작하지 않는다
    with pytest.raises(ValueError, match="nutrition"):
        ModelRouter({k: v for k, v in SPECS.items() if k != "nutrition"}, {"big": None, "small": None})


def test_router_routes_by_agent_and_retries_invalid_output():
    fake = FakeChatClient({"SupervisorDecision": ["{not json", {"next": "recipe"}]})
    router = ModelRouter(SPECS, {"big": fake, "small": fake}, retries=1)
    out = router.invoke("supervisor", SupervisorDecision, "sys", "user")
    assert out.next == "recipe"
    assert fake.calls == [("supervisor-lora", "SupervisorDecision")] * 2
    assert [e.ok for e in router.tracer.events] == [False, True]


def test_router_gives_up_after_retries():
    fake = FakeChatClient({"SupervisorDecision": ["{}", "{}"]})
    router = ModelRouter(SPECS, {"big": fake, "small": fake}, retries=1)
    with pytest.raises(StructuredOutputError):
        router.invoke("supervisor", SupervisorDecision, "sys", "user")


def test_openai_compatible_client_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"next": "finish"}'}}]})

    client = OpenAICompatibleClient("http://vllm/v1", transport=httpx.MockTransport(handler))
    router = ModelRouter(SPECS, {"big": client, "small": client})
    out = router.invoke("supervisor", SupervisorDecision, "sys", "user")

    assert out.next == "finish"
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["model"] == "supervisor-lora"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "SupervisorDecision"


def test_load_router_from_example_config():
    router = load_router("config/models.example.toml")
    assert router.specs["supervisor"].model_name == "supervisor-lora"
    assert router.specs["ingredient"].endpoint == "small"
