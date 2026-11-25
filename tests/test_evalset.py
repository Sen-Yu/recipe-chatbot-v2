"""평가 데이터셋 형식 검사 (로드맵 1.1, 1.3의 자동 검사 부분)."""

import json

import pytest

from recipe_chatbot.evalset import ROLES, check_dataset, load_cases

V0 = "data/eval/v0"


def test_v0_loads_and_ids_are_unique():
    counts = check_dataset(V0)
    assert set(counts) == set(ROLES)
    assert all(n > 0 for n in counts.values())


def test_bad_line_reports_line_number(tmp_path):
    path = tmp_path / "supervisor.jsonl"
    path.write_text(json.dumps({"id": "sup-001", "source": "x", "utterance": "u",
                                "expected": {"next_any_of": ["fly"]}}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="supervisor.jsonl:1"):
        load_cases(path, "supervisor")
