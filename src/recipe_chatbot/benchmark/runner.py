"""벤치마크 실행: 평가 세트를 역할별로 돌리고, 지표를 모아 보고서를 만든다.

지표 정의는 docs/benchmark.md 에 있다.
"""

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..evalset import ROLES, Role, load_cases
from ..models import ModelRouter
from ..nodes import Deps
from ..tracing import Tracer
from .scoring import SCORERS

MAX_INFRA_STREAK = 5  # 연결 오류가 연속으로 이만큼 나면 엔드포인트가 죽은 것으로 보고 멈춘다

AGENT_OF_ROLE = {
    "supervisor": "supervisor",
    "ingredient": "ingredient",
    "recipe": "recipe",
    "nutrition": "nutrition",
    "selection": "selector",
}


@dataclass
class CaseResult:
    id: str
    role: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    error: str | None = None  # 구조화 출력 실패, 연결 오류 등
    model_calls: int = 0
    first_try_ok: bool | None = None  # 첫 모델 호출이 바로 유효한 출력을 냈는지 (모델을 안 불렀으면 None)
    model_ms: float = 0.0
    output: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1], 1)


def run_case(role: Role, case, deps: Deps) -> CaseResult:
    tracer = deps.router.tracer
    start = len(tracer.events)
    result = CaseResult(id=case.id, role=role, passed=False, tags=case.tags)
    try:
        score = SCORERS[role](case, deps)
        result.passed, result.checks, result.output = score.passed, score.checks, score.output
    except Exception as e:  # 한 케이스가 실패해도 나머지는 계속 돈다
        result.error = f"{type(e).__name__}: {e}"[:300]
    events = [e for e in tracer.events[start:] if e.kind == "model"]
    result.model_calls = len(events)
    result.model_ms = round(sum(e.ms for e in events), 1)
    if events:
        result.first_try_ok = events[0].ok
    return result


def _is_structured_fail(r: CaseResult) -> bool:
    return bool(r.error) and r.error.startswith("StructuredOutputError")


def _is_infra(r: CaseResult) -> bool:
    return bool(r.error) and not _is_structured_fail(r)


def _rate(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 3) if flags else None


def summarize(results: list[CaseResult]) -> dict:
    out: dict = {}
    for role in ROLES:
        rs = [r for r in results if r.role == role]
        if not rs:
            continue
        called = [r for r in rs if r.model_calls]
        check_names = sorted({k for r in rs for k in r.checks})
        out[role] = {
            "cases": len(rs),
            "pass_rate": round(sum(r.passed for r in rs) / len(rs), 3),
            # 연결 오류처럼 모델 밖의 실패는 구조화 성공률에서 빼고 따로 센다
            "infra_errors": sum(_is_infra(r) for r in rs),
            "structured_ok_rate": _rate([not _is_structured_fail(r) for r in rs if not _is_infra(r)]),
            "first_try_rate": round(sum(bool(r.first_try_ok) for r in called) / len(called), 3)
            if called
            else None,
            "model_calls": sum(r.model_calls for r in rs),
            "latency_ms_p50": _pct([r.model_ms for r in called], 50),
            "latency_ms_p95": _pct([r.model_ms for r in called], 95),
            "checks": {
                k: round(
                    sum(r.checks.get(k, False) for r in rs if k in r.checks) / sum(k in r.checks for r in rs),
                    3,
                )
                for k in check_names
            },
        }
    return out


def run(
    router: ModelRouter,
    data_dir: str | Path,
    roles: list[Role] | None = None,
    deps: Deps | None = None,
    limit: int | None = None,
    meta: dict | None = None,
) -> dict:
    """평가 세트를 돌리고 보고서(dict)를 돌려준다."""
    router.tracer = router.tracer or Tracer()
    deps = deps or Deps(router=router)
    started, t0 = datetime.now(UTC), time.perf_counter()
    results: list[CaseResult] = []
    aborted = ""
    for role in roles or list(ROLES):
        for case in load_cases(Path(data_dir) / f"{role}.jsonl", role)[:limit]:
            results.append(run_case(role, case, deps))
            recent = results[-MAX_INFRA_STREAK:]
            if len(recent) == MAX_INFRA_STREAK and all(_is_infra(r) for r in recent):
                aborted = f"연속 {MAX_INFRA_STREAK}회 연결 오류로 중단: {recent[-1].error}"
                break
        if aborted:
            break
    return {
        "meta": {
            "started": started.isoformat(timespec="seconds"),
            "seconds": round(time.perf_counter() - t0, 1),
            "data": str(data_dir),
            "models": {role: router.specs[a].model_name for role, a in AGENT_OF_ROLE.items()},
            **({"aborted": aborted} if aborted else {}),
            **(meta or {}),
        },
        "summary": summarize(results),
        "cases": [asdict(r) for r in results],
    }


def to_markdown(report: dict) -> str:
    meta, summary = report["meta"], report["summary"]
    lines = [
        f"# 벤치마크 결과 · {meta.get('name', '')}".rstrip(" ·"),
        "",
        f"- 시작: {meta['started']} · 걸린 시간: {meta['seconds']}초 · 데이터: `{meta['data']}`",
    ]
    if meta.get("aborted"):
        lines.append(f"- ⚠️ {meta['aborted']}")
    if meta.get("note"):
        lines.append(f"- 메모: {meta['note']}")
    lines += [
        "",
        "| 역할 | 모델 | 케이스 | 정답률 | 구조화 성공 | 첫 시도 성공 "
        "| 지연 p50 (ms) | 지연 p95 (ms) | 연결 오류 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    fmt = lambda v: "—" if v is None else (f"{v:.0%}" if isinstance(v, float) and v <= 1 else str(v))  # noqa: E731
    for role, s in summary.items():
        lines.append(
            f"| {role} | `{meta['models'].get(role, '')}` | {s['cases']} | {fmt(s['pass_rate'])} | "
            f"{fmt(s['structured_ok_rate'])} | {fmt(s['first_try_rate'])} | "
            f"{s['latency_ms_p50'] if s['latency_ms_p50'] is not None else '—'} | "
            f"{s['latency_ms_p95'] if s['latency_ms_p95'] is not None else '—'} | {s['infra_errors']} |"
        )
    lines += ["", "## 항목별 통과율", "", "| 역할 | 항목 | 통과율 |", "|---|---|---|"]
    for role, s in summary.items():
        for k, v in s["checks"].items():
            lines.append(f"| {role} | {k} | {v:.0%} |")
    failed = [c for c in report["cases"] if not c["passed"]]
    if failed:
        lines += [
            "",
            f"## 틀린 케이스 ({len(failed)}개)",
            "",
            "| id | 틀린 항목 | 오류 | 모델 출력 |",
            "|---|---|---|---|",
        ]
        for c in failed:
            bad = ", ".join(k for k, v in c["checks"].items() if not v)
            out = json.dumps(c["output"], ensure_ascii=False)[:120].replace("|", "\\|")
            lines.append(f"| {c['id']} | {bad or '—'} | {(c['error'] or '—')[:80]} | {out} |")
    return "\n".join(lines) + "\n"


def compare(reports: list[dict]) -> str:
    """여러 실행 결과를 역할 × 실행 표로 비교한다 (정답률 / 첫 시도 성공 / 지연 p50)."""
    names = [r["meta"].get("name") or r["meta"]["started"] for r in reports]
    lines = [
        "# 벤치마크 비교",
        "",
        "칸 안: 정답률 · 첫 시도 성공률 · 지연 p50(ms)",
        "",
        "| 역할 | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for role in ROLES:
        cells = []
        for r in reports:
            s = r["summary"].get(role)
            if not s:
                cells.append("—")
                continue
            ft = "—" if s["first_try_rate"] is None else f"{s['first_try_rate']:.0%}"
            lat = "—" if s["latency_ms_p50"] is None else f"{s['latency_ms_p50']:.0f}"
            cells.append(f"{s['pass_rate']:.0%} · {ft} · {lat}")
        lines.append(f"| {role} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
