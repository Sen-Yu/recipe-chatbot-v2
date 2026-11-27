"""레시피 RAG 검색 인덱스 (업무 플로우 L.5).

수집·정제한 `rag.jsonl`(공통 형식 `SourceRecipe`)을 임베딩해서 인덱스로 저장하고,
레시피 에이전트의 검색 도구(`RecipeRetriever.search`)를 벡터 검색으로 바꾼다.

- 임베딩: BAAI/bge-m3 (v1과 같은 모델, 로컬). 정규화한 벡터라 내적 = 코사인 유사도.
- 저장: `vectors.npy`(float32) + `recipes.jsonl`(검색 결과로 돌려줄 레시피) + `meta.json`.
  레시피 수천 개 규모라 별도 벡터 DB 없이 numpy로 충분하다.
- 임베딩 모델은 조용히 다른 모델로 바꾸지 않는다. 인덱스의 모델 이름과 다르면 불러오지 않는다
  (v1 원칙: 벡터 공간이 섞이면 안 된다).

    uv sync --group rag
    uv run python -m recipe_chatbot.rag build data/ingest/out/rag.jsonl --out data/rag
    uv run python -m recipe_chatbot.rag search data/rag 김치 두부 돼지고기
"""

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np

from .domain.ingredient_synonyms import normalize_ingredient_name
from .ingest.records import SourceRecipe
from .schemas import StructuredRecipe

DEFAULT_MODEL = "BAAI/bge-m3"
OVERLAP_WEIGHT = 0.05  # 보유 재료가 레시피에 하나 들어 있을 때마다 더하는 점수


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """(len(texts), dim) 크기의 정규화한 float32 벡터."""
        ...


class BGEEmbedder:
    """BAAI/bge-m3 로컬 임베딩. sentence-transformers가 필요하다 (`uv sync --group rag`)."""

    def __init__(self, name: str = DEFAULT_MODEL, device: str | None = None):
        from sentence_transformers import SentenceTransformer  # 무거워서 쓸 때만 불러온다

        if device is None:
            import torch

            device = (
                "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
            )
        self.name = name
        self._model = SentenceTransformer(name, device=device)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(
            texts, normalize_embeddings=True, batch_size=32, show_progress_bar=len(texts) > 64
        )
        return np.asarray(vecs, dtype=np.float32)


def document_text(r: SourceRecipe) -> str:
    """임베딩할 글: 요리 이름, 재료, 조리 순서 앞부분. 재료로 찾는 질의가 많아서 재료를 앞쪽에 둔다."""
    names = ", ".join(dict.fromkeys(i.name for i in r.ingredients))
    steps = " ".join(r.steps[:3])[:300]
    return f"{r.title}\n재료: {names}\n{steps}"


def query_text(ingredient_names: Iterable[str], request: str = "") -> str:
    names = ", ".join(ingredient_names)
    return f"{request}\n재료: {names}".strip() if request else f"재료: {names}"


def _norm_names(r: StructuredRecipe) -> set[str]:
    return {normalize_ingredient_name(i.name.replace(" ", "")) for i in r.ingredients}


class VectorRetriever:
    """`RecipeRetriever`와 같은 모양의 벡터 검색기."""

    def __init__(self, embedder: Embedder, vectors: np.ndarray, recipes: list[StructuredRecipe]):
        if len(vectors) != len(recipes):
            raise ValueError("벡터 수와 레시피 수가 다릅니다")
        self.embedder = embedder
        self.vectors = vectors
        self.recipes = recipes
        self._names = [_norm_names(r) for r in recipes]

    def search(self, ingredient_names: list[str], k: int = 3, request: str = "") -> list[StructuredRecipe]:
        if not self.recipes or not (ingredient_names or request):
            return []
        q = self.embedder.embed([query_text(ingredient_names, request)])[0]
        scores = self.vectors @ q
        owned = {normalize_ingredient_name(n.replace(" ", "")) for n in ingredient_names}
        if owned:  # 의미가 비슷해도 가진 재료를 더 많이 쓰는 레시피를 앞으로
            scores = scores + OVERLAP_WEIGHT * np.array([len(owned & names) for names in self._names])
        # 같은 이름의 레시피는 한 번만 ('고등어조림 | 고등어조림 | 고등어조림' 대신 서로 다른 후보)
        picked: list[StructuredRecipe] = []
        seen: set[str] = set()
        for i in np.argsort(-scores):
            title = self.recipes[i].title.replace(" ", "")
            if title not in seen:
                seen.add(title)
                picked.append(self.recipes[i])
                if len(picked) == k:
                    break
        return picked

    # ---- 저장 · 불러오기 ----
    @classmethod
    def build(cls, embedder: Embedder, sources: list[SourceRecipe]) -> "VectorRetriever":
        vectors = (
            embedder.embed([document_text(r) for r in sources]) if sources else np.zeros((0, 1), np.float32)
        )
        return cls(embedder, vectors, [r.to_structured() for r in sources])

    def save(self, out: Path | str) -> Path:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        np.save(out / "vectors.npy", self.vectors.astype(np.float32))
        with (out / "recipes.jsonl").open("w", encoding="utf-8") as f:
            for r in self.recipes:
                f.write(r.model_dump_json() + "\n")
        meta = {
            "model": self.embedder.name,
            "count": len(self.recipes),
            "dim": int(self.vectors.shape[1]) if len(self.vectors) else 0,
            "built": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: Path | str, embedder: Embedder) -> "VectorRetriever":
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        if meta["model"] != embedder.name:
            raise ValueError(f"인덱스 임베딩 모델({meta['model']})과 지금 모델({embedder.name})이 다릅니다")
        recipes = [
            StructuredRecipe.model_validate_json(line)
            for line in (path / "recipes.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(embedder, np.load(path / "vectors.npy"), recipes)


def load_retriever(index_dir: Path | str | None = None):
    """그래프에 넣을 검색기. 인덱스 폴더(인자 또는 RECIPE_RAG_INDEX)가 있으면 벡터 검색, 없으면 빈 검색기."""
    import os

    from .tools import InMemoryRetriever

    path = Path(index_dir or os.environ.get("RECIPE_RAG_INDEX", "data/rag"))
    if not (path / "meta.json").exists():
        return InMemoryRetriever()
    return VectorRetriever.load(path, BGEEmbedder(json.loads((path / "meta.json").read_text())["model"]))


def read_sources(path: Path | str) -> list[SourceRecipe]:
    return [
        SourceRecipe.model_validate_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m recipe_chatbot.rag", description="레시피 RAG 인덱스")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="rag.jsonl로 인덱스 만들기")
    p.add_argument("input", help="정제 결과 rag.jsonl")
    p.add_argument("--out", default="data/rag")
    p = sub.add_parser("search", help="재료로 검색해 보기")
    p.add_argument("index")
    p.add_argument("ingredients", nargs="+")
    p.add_argument("-k", type=int, default=5)
    args = parser.parse_args(argv)

    embedder = BGEEmbedder()
    if args.cmd == "build":
        sources = read_sources(args.input)
        out = VectorRetriever.build(embedder, sources).save(args.out)
        print(f"레시피 {len(sources)}개 → {out}")
        return 0
    retriever = VectorRetriever.load(args.index, embedder)
    for r in retriever.search(args.ingredients, k=args.k):
        names = ", ".join(i.name for i in r.ingredients[:8])
        print(f"- {r.title} [{r.recipe_id}] · {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
