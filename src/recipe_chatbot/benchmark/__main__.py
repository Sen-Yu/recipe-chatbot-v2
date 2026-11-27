"""벤치마크 명령.

후보 모델 하나를 모든 역할에 (후보 비교용):
    uv run python -m recipe_chatbot.benchmark run --base-url http://localhost:11434/v1 --model qwen2.5:7b \
        --name qwen2.5-7b --note "M2 Pro 32GB, Ollama"

큰 모델(수퍼바이저·레시피)과 작은 모델(재료·영양·선택)을 나눠서:
    uv run python -m recipe_chatbot.benchmark run --base-url http://localhost:8000/v1 \
        --big-model BIG --small-model SMALL --small-base-url http://localhost:11434/v1 --name big+small

설정 파일 그대로 (어댑터 포함):
    uv run python -m recipe_chatbot.benchmark run --config config/models.toml --name with-adapters

여러 결과 비교:
    uv run python -m recipe_chatbot.benchmark compare reports/benchmark/*/report.json
"""

import argparse
import json
import sys
from pathlib import Path

from ..evalset import ROLES
from ..models import ModelRouter, ModelSpec, OpenAICompatibleClient, load_router
from .runner import compare, run, to_markdown

BIG_AGENTS = ("supervisor", "recipe")
SMALL_AGENTS = ("ingredient", "nutrition", "selector")


def build_router(args) -> ModelRouter:
    if args.config:
        return load_router(args.config)
    big = args.big_model or args.model
    small = args.small_model or args.model
    if not (big and small and args.base_url):
        raise SystemExit("--config 또는 --base-url과 --model(또는 --big-model/--small-model)이 필요합니다")
    clients = {"big": OpenAICompatibleClient(args.base_url, timeout=args.timeout)}
    clients["small"] = (
        OpenAICompatibleClient(args.small_base_url, timeout=args.timeout)
        if args.small_base_url
        else clients["big"]
    )
    specs = {a: ModelSpec("big", big) for a in BIG_AGENTS} | {
        a: ModelSpec("small", small) for a in SMALL_AGENTS
    }
    return ModelRouter(specs, clients)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m recipe_chatbot.benchmark", description="역할별 벤치마크")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="평가 세트를 모델에 돌리기")
    p.add_argument("--config", help="에이전트별 모델 설정 파일 (config/models.toml 형식)")
    p.add_argument("--base-url", help="OpenAI 호환 엔드포인트 (예: http://localhost:11434/v1)")
    p.add_argument("--model", help="모든 역할에 쓸 모델 이름")
    p.add_argument("--big-model", help="수퍼바이저·레시피 모델 (--model 대신)")
    p.add_argument("--small-model", help="재료·영양·선택 모델 (--model 대신)")
    p.add_argument("--small-base-url", help="작은 모델 엔드포인트가 다를 때")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--data", default="data/eval/v1", help="평가 세트 폴더")
    p.add_argument("--roles", help=f"쉼표로 구분 ({','.join(ROLES)})")
    p.add_argument("--limit", type=int, help="역할마다 앞에서 N개만 (빠른 확인용)")
    p.add_argument("--name", default="run", help="결과 폴더 이름")
    p.add_argument("--note", default="", help="실행 환경 메모 (GPU, VRAM, 양자화 등)")
    p.add_argument("--out", default="reports/benchmark", help="결과를 둘 폴더")

    p = sub.add_parser("compare", help="여러 결과(report.json)를 표로 비교")
    p.add_argument("reports", nargs="+")
    p.add_argument("--out", help="비교 표를 저장할 md 파일")

    args = parser.parse_args(argv)

    if args.cmd == "compare":
        table = compare([json.loads(Path(r).read_text(encoding="utf-8")) for r in args.reports])
        if args.out:
            Path(args.out).write_text(table, encoding="utf-8")
        print(table)
        return 0

    router = build_router(args)
    roles = args.roles.split(",") if args.roles else None
    report = run(
        router, args.data, roles=roles, limit=args.limit, meta={"name": args.name, "note": args.note}
    )
    out = Path(args.out) / args.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(report)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(md.split("\n## 항목별")[0])
    print(f"결과: {out}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
