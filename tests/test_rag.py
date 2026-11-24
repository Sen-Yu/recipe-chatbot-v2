"""RAG 인덱스: 저장·불러오기, 검색 순서, 다른 임베딩 모델 거부.

실제 모델 대신 글자 기반 가짜 임베딩을 쓴다.
"""

import numpy as np
import pytest

from recipe_chatbot.ingest.amounts import parse_line
from recipe_chatbot.ingest.records import SourceRecipe, Usage
from recipe_chatbot.rag import VectorRetriever


class CharEmbedder:
    """글자 빈도 벡터. 같은 글자를 많이 공유할수록 가깝다."""

    def __init__(self, name="fake-char"):
        self.name = name

    def embed(self, texts):
        out = np.zeros((len(texts), 4096), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                out[i, ord(ch) % 4096] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


def recipe(sid, title, ingredients):
    return SourceRecipe(
        source="test", source_id=sid, title=title, ingredients=[parse_line(x) for x in ingredients],
        steps=["손질한다", "익힌다"], usage=Usage(rag=True, train=False, license="test"),
    )  # fmt: skip


def test_build_save_load_search(tmp_path):
    sources = [
        recipe("1", "김치찌개", ["김치 200g", "돼지고기 100g", "두부 1/2모"]),
        recipe("2", "감자조림", ["감자 2개", "간장 3큰술"]),
        recipe("3", "계란말이", ["계란 3개", "대파 1/2대"]),
        recipe("4", "김치 찌개", ["김치 300g", "돼지고기 200g", "두부 1모"]),
    ]
    VectorRetriever.build(CharEmbedder(), sources).save(tmp_path / "idx")
    r = VectorRetriever.load(tmp_path / "idx", CharEmbedder())

    assert r.search(["김치", "두부"], k=1)[0].title.replace(" ", "") == "김치찌개"
    assert r.search(["계란"], k=1)[0].recipe_id == "test:3"
    assert r.search([], request="감자조림 알려줘", k=1)[0].title == "감자조림"
    assert r.search([]) == []
    # 같은 이름은 한 번만 보여 준다 (띄어쓰기만 다른 이름 포함)
    titles = [x.title for x in r.search(["김치", "돼지고기", "두부"], k=2)]
    assert titles[0].replace(" ", "") == "김치찌개" and titles[1].replace(" ", "") != "김치찌개"

    with pytest.raises(ValueError, match="임베딩 모델"):
        VectorRetriever.load(tmp_path / "idx", CharEmbedder(name="other-model"))
