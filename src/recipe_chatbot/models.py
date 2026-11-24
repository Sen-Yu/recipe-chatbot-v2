"""모델 라우터: 에이전트 이름 → (베이스 모델, 어댑터) → 구조화 출력.

vLLM과 Ollama 모두 OpenAI 호환 /v1/chat/completions 를 쓰므로 클라이언트는 하나다.
테스트와 벤치마크 전 개발에서는 FakeChatClient 로 바꿔 끼운다.
"""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .tracing import Tracer

T = TypeVar("T", bound=BaseModel)

AGENTS = ("supervisor", "ingredient", "recipe", "nutrition", "selector")


@dataclass(frozen=True)
class ModelSpec:
    endpoint: str
    base: str
    adapter: str | None = None
    served_name: str | None = None

    @property
    def model_name(self) -> str:
        """요청에 쓰는 모델 이름: served_name > adapter > base."""
        return self.served_name or self.adapter or self.base


class ChatClient(Protocol):
    def complete_json(self, model: str, system: str, user: str, schema: dict[str, Any]) -> str:
        """JSON 문자열 하나를 돌려준다."""
        ...


class StructuredOutputError(RuntimeError):
    pass


class OpenAICompatibleClient:
    """vLLM, Ollama 공용 클라이언트."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def complete_json(self, model: str, system: str, user: str, schema: dict[str, Any]) -> str:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.get("title", "output"), "schema": schema},
            },
        }
        r = self._http.post("/chat/completions", json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class FakeChatClient:
    """스키마 이름별로 정해진 응답을 순서대로 돌려주는 가짜 모델.

    responses: {"SupervisorDecision": [dict | BaseModel | str, ...], ...}
    calls: 실제로 불린 (모델 이름, 스키마 이름) 기록
    """

    def __init__(self, responses: dict[str, list[Any]]):
        self._queues = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, model: str, system: str, user: str, schema: dict[str, Any]) -> str:
        name = schema.get("title", "")
        self.calls.append((model, name))
        queue = self._queues.get(name)
        if not queue:
            raise AssertionError(f"FakeChatClient: '{name}' 응답이 더 없습니다")
        item = queue.pop(0)
        if isinstance(item, BaseModel):
            return item.model_dump_json()
        if isinstance(item, str):
            return item
        return json.dumps(item, ensure_ascii=False)


class ModelRouter:
    def __init__(
        self,
        specs: dict[str, ModelSpec],
        clients: dict[str, ChatClient],
        tracer: Tracer | None = None,
        retries: int = 1,
    ):
        missing = [a for a in AGENTS if a not in specs]
        if missing:
            raise ValueError(f"모델 설정이 없는 에이전트: {missing}")
        for agent, spec in specs.items():
            if spec.endpoint not in clients:
                raise ValueError(f"{agent}: 엔드포인트 '{spec.endpoint}' 클라이언트가 없습니다")
        self.specs, self.clients = specs, clients
        self.tracer = tracer or Tracer()
        self.retries = retries

    def invoke(self, agent: str, schema: type[T], system: str, user: str) -> T:
        spec = self.specs[agent]
        client = self.clients[spec.endpoint]
        json_schema = schema.model_json_schema()
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            with self.tracer.span(
                "model", agent, model=spec.model_name, schema=schema.__name__, attempt=attempt
            ) as ev:
                raw = client.complete_json(spec.model_name, system, user, json_schema)
                try:
                    result = schema.model_validate_json(raw)
                except ValidationError as e:
                    ev.ok = False
                    ev.detail["error"] = "구조화 출력 검증 실패"
                    last_error = e
                    continue
                ev.detail["output"] = result.model_dump(mode="json")
                return result
        raise StructuredOutputError(f"{agent}: 구조화 출력 실패 ({schema.__name__})") from last_error


def load_router(path: str | Path, tracer: Tracer | None = None) -> ModelRouter:
    """config/models.toml 형식에서 라우터를 만든다."""
    conf = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    clients: dict[str, ChatClient] = {
        name: OpenAICompatibleClient(ep["base_url"], api_key=ep.get("api_key"))
        for name, ep in conf.get("endpoints", {}).items()
    }
    specs = {
        agent: ModelSpec(
            endpoint=a["endpoint"], base=a["base"], adapter=a.get("adapter"), served_name=a.get("served_name")
        )
        for agent, a in conf.get("agents", {}).items()
    }
    return ModelRouter(specs, clients, tracer=tracer)
