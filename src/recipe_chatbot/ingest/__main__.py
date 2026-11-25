"""레시피 수집 명령.

uv run python -m recipe_chatbot.ingest discover 10000recipe --keywords 볶음밥,잡채 --pages 2
uv run python -m recipe_chatbot.ingest discover 10000recipe --random 10
uv run python -m recipe_chatbot.ingest fetch 10000recipe --limit 50
uv run python -m recipe_chatbot.ingest discover foodsafety          # FOODSAFETY_API_KEY 필요
uv run python -m recipe_chatbot.ingest parse 10000recipe [--reparse]
uv run python -m recipe_chatbot.ingest build
uv run python -m recipe_chatbot.ingest status
"""

import argparse
import json
import random
import sys

from . import pipeline, refine
from .keywords import FOOD_KEYWORDS
from .sources import SOURCES
from .store import Store

DEFAULT_ROOT = "data/ingest"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m recipe_chatbot.ingest", description="레시피 수집")
    parser.add_argument("--root", default=DEFAULT_ROOT, help=f"상태표·원본·결과 폴더 (기본 {DEFAULT_ROOT})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover", help="새 레시피 id 찾기")
    p.add_argument("source", choices=SOURCES)
    p.add_argument("--keywords", help="쉼표로 구분한 검색어 (10000recipe)")
    p.add_argument("--random", type=int, help="키워드 목록에서 무작위로 N개 (10000recipe)")
    p.add_argument("--pages", type=int, default=2, help="검색어마다 볼 목록 페이지 수 (10000recipe)")
    p.add_argument("--limit", type=int, help="가져올 최대 레코드 수 (foodsafety)")

    p = sub.add_parser("fetch", help="찾은 id의 원본 받기")
    p.add_argument("source", choices=SOURCES)
    p.add_argument("--limit", type=int, help="이번에 받을 최대 개수")

    p = sub.add_parser("parse", help="원본을 공통 형식으로 바꾸기")
    p.add_argument("source", choices=SOURCES)
    p.add_argument("--reparse", action="store_true", help="이미 파싱한 것도 원본에서 다시 만들기")

    sub.add_parser("build", help="정제·중복 제거 후 rag.jsonl, train.jsonl 만들기")
    sub.add_parser("status", help="출처별 단계 현황")

    args = parser.parse_args(argv)
    store = Store(args.root)
    try:
        if args.cmd == "status":
            print(json.dumps(store.counts(), ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "build":
            print(json.dumps(refine.build(store), ensure_ascii=False, indent=2))
            return 0

        source = SOURCES[args.source]()
        if args.cmd == "parse":
            r = pipeline.parse(source, store, reparse=args.reparse)
            print(f"파싱 {r.done}개, 레시피 아님 {r.skipped}개, 실패 {r.failed}개")
            return 0

        with pipeline.make_client() as client:
            if args.cmd == "discover":
                keywords = args.keywords.split(",") if args.keywords else None
                if args.random:
                    keywords = random.sample(FOOD_KEYWORDS, min(args.random, len(FOOD_KEYWORDS)))
                n = pipeline.discover(
                    source, client, store, keywords=keywords, pages=args.pages, limit=args.limit
                )
                print(f"새 id {n}개")
                return 0
            r = pipeline.fetch(source, client, store, limit=args.limit)
            print(f"받기 {r.done}개, 실패 {r.failed}개" + (f" — {r.stopped}" if r.stopped else ""))
            return 1 if r.stopped else 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
