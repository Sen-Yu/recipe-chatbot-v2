# 레시피 수집 (v1에서 발췌)

v1 레포 `data_pipeline/`에 있던 레시피 수집 코드를 옮겼습니다. v1 커밋 `894f3a6` 기준입니다. RAG 검색 인덱스와 레시피 어댑터 학습에 쓸 레시피를 더 모으기 위한 것이고, 업무 플로우의 L.4 업무입니다.

> ⚠️ 수집한 원본(PDF)과 구조화 결과(JSON)는 만개의 레시피 사용자의 저작물입니다. 레포에 커밋하지 않습니다(`.gitignore`). 학습에 쓸지는 출처와 이용 범위를 정한 뒤 결정합니다.

## v1 파이프라인 구조

v1은 네 단계를 asyncio 큐로 이어서 동시에 돌렸습니다.

| 단계 | v1 파일 | 하는 일 | 결과물 | v2 상태 |
|---|---|---|---|---|
| 1. 수집 | `collect_recipe_links.py`, `food_keywords.py` | 요리 키워드로 검색 결과 페이지를 돌며 레시피 ID 수집 | `collected/recipe_ids.json` | 옮김 (그대로) |
| 2. PDF 저장 | `save_recipes_pdf.py` | 레시피 인쇄 페이지를 PDF로 저장 | `original_recipes/*.pdf` | 옮김 (그대로) |
| 3. 구조화 | `structured.py` | PDF 글자를 LLM으로 정형 JSON 변환 | `structured_recipes/<id>.json` | 스키마와 프롬프트만 옮김 (`structure_prompt.py`) |
| 4. VDB 저장 | `recipe_pipeline.py`, `rag/` | bge-m3 임베딩 후 Chroma에 저장 | `data/vdb/` | 안 옮김 |

3단계는 v1의 LLM 래퍼(`llm.py`)에, 4단계는 v1의 `rag/` 모듈에 묶여 있어서 그대로는 돌지 않습니다. 구조화 모델과 v2 검색 인덱스를 정한 뒤 다시 연결합니다. 네 단계를 묶던 `recipe_pipeline.py`도 그때 함께 옮깁니다.

## v1이 모은 양

| 항목 | 수 |
|---|---|
| 수집한 레시피 ID | 80개 ("볶음밥" 검색 결과) |
| PDF 저장 | 28개 (모두 볶음밥류) |
| 구조화 JSON | 28개 |
| 검색 키워드 | 99개 (`food_keywords.py`) |

## 실행 (1·2단계)

```bash
uv sync --group crawl
uv run playwright install chromium

# "볶음밥" 검색 결과 1~3페이지에서 레시피 ID 수집
uv run python data_pipeline/collect_recipe_links.py "볶음밥" 3

# 모은 ID 중 0~49번째를 PDF로 저장
uv run python data_pipeline/save_recipes_pdf.py 0 50
```

## v1에서 지키던 규칙 (그대로 유지)

- 요청 사이 대기: 검색 페이지 2.5초, 레시피 PDF 5초. 줄이지 않습니다.
- User-Agent에 `personal-research-script`를 붙여 개인 연구용 스크립트임을 밝힙니다.
- 검색 결과가 비면(차단 가능성) 수집을 멈추고, PDF 저장이 연속 5번 실패하면 멈춥니다.
- 이미 저장한 PDF와 구조화한 JSON은 건너뜁니다. 중단 후 다시 실행해도 됩니다.
