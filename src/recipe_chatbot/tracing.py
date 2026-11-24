"""에이전트 호출 순서 기록.

디버깅용이면서, 로드맵 4.1(어댑터 학습 데이터)의 출처가 된다.
"""

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TraceEvent:
    kind: str  # node | model
    name: str
    ok: bool = True
    ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


class Tracer:
    def __init__(self, path: str | Path | None = None):
        self.events: list[TraceEvent] = []
        self.path = Path(path) if path else None

    @contextmanager
    def span(self, kind: str, name: str, **detail: Any):
        event = TraceEvent(kind=kind, name=name, detail=detail)
        start = time.perf_counter()
        try:
            yield event
        except Exception as e:
            event.ok = False
            event.detail["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            event.ms = round((time.perf_counter() - start) * 1000, 2)
            self._record(event)

    def _record(self, event: TraceEvent) -> None:
        self.events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")

    def names(self, kind: str | None = None) -> list[str]:
        return [e.name for e in self.events if kind is None or e.kind == kind]
