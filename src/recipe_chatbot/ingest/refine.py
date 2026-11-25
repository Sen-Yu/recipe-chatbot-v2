"""정제: 품질 거르기 → 중복 제거 → 용도별로 나누기.

파싱 결과(parsed/)만 읽으므로 사이트에 요청하지 않는다. 규칙을 바꾸면 이 단계만 다시 돌린다.

    out/rag.jsonl    usage.rag 인 레코드 (RAG 검색 인덱스 재료)
    out/train.jsonl  usage.train 인 레코드 (레시피 어댑터 학습 후보)
    out/report.json  출처별 개수, 걸러진 이유, 중복 수, 해석 못 한 재료 줄 수,
                     재료 양이 하나도 없는 레시피 수 (원본에 양이 없음. 검색엔 쓰되 학습에선 거를 후보)
"""

import json
import re
from collections import Counter
from pathlib import Path

from ..domain.ingredient_synonyms import normalize_ingredient_name
from .records import SourceRecipe
from .store import Store

MIN_INGREDIENTS = 2
MIN_STEPS = 2
MAX_UNPARSED_RATIO = 0.5  # 재료 줄의 절반 넘게 해석 못 하면 버린다


def reject_reason(r: SourceRecipe) -> str | None:
    if not r.title:
        return "제목 없음"
    if len(r.ingredients) < MIN_INGREDIENTS:
        return f"재료 {MIN_INGREDIENTS}개 미만"
    if len(r.steps) < MIN_STEPS:
        return f"조리 단계 {MIN_STEPS}개 미만"
    unparsed = sum(not i.parsed for i in r.ingredients)
    if unparsed / len(r.ingredients) > MAX_UNPARSED_RATIO:
        return "재료 줄 해석 실패가 절반 넘음"
    return None


def dedup_key(r: SourceRecipe) -> str:
    """제목(띄어쓰기 무시)과 재료 이름 집합이 같으면 같은 레시피로 본다."""
    title = re.sub(r"\s+", "", r.title)
    names = sorted({normalize_ingredient_name(i.name.replace(" ", "")) for i in r.ingredients})
    return title + "|" + ",".join(names)


def _preference(r: SourceRecipe) -> tuple:
    # 중복이면 학습에 쓸 수 있는 출처, 그다음 평가가 많은 쪽을 남긴다
    return (r.usage.train, r.rating_count or 0, r.rating or 0)


def build(store: Store, out_dir: Path | str | None = None) -> dict:
    out = Path(out_dir) if out_dir else store.root / "out"
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "input": Counter(),
        "rejected": Counter(),
        "duplicates": 0,
        "unparsed_lines": 0,
        "without_amounts": 0,
    }
    kept: dict[str, SourceRecipe] = {}
    for r in store.iter_parsed():
        report["input"][r.source] += 1
        report["unparsed_lines"] += sum(not i.parsed for i in r.ingredients)
        report["without_amounts"] += all(i.amount is None for i in r.ingredients)
        reason = reject_reason(r)
        if reason:
            report["rejected"][reason] += 1
            continue
        key = dedup_key(r)
        if key in kept:
            report["duplicates"] += 1
            if _preference(r) <= _preference(kept[key]):
                continue
        kept[key] = r

    recipes = sorted(kept.values(), key=lambda r: r.key)
    outputs = {"rag": [r for r in recipes if r.usage.rag], "train": [r for r in recipes if r.usage.train]}
    for name, rows in outputs.items():
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(r.model_dump_json() + "\n")

    summary = {
        "input": dict(report["input"]),
        "rejected": dict(report["rejected"]),
        "duplicates": report["duplicates"],
        "unparsed_lines": report["unparsed_lines"],
        "without_amounts": report["without_amounts"],
        "kept": len(recipes),
        "rag": len(outputs["rag"]),
        "train": len(outputs["train"]),
        "by_source": dict(Counter(r.source for r in recipes)),
    }
    (out / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
