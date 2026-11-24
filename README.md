# 레시피 챗봇 v2

보유한 재료와 조건으로 요리 레시피를 추천하는 동적 멀티 에이전트 챗봇입니다.

> v1: [KTB4-emet-LLM-Application-Project](https://github.com/100-hours-a-week/KTB4-emet-LLM-Application-Project). 고정 LangGraph로 만든 재료 기반 레시피 챗봇

## 상태

기반 코드 단계입니다. 그래프와 에이전트는 가짜 모델로 동작을 검증했고, 실제 모델은 벤치마크 후 연결합니다.

## 개발

```bash
uv sync                 # Python 3.14 + 의존성 설치
uv run pytest -q        # 테스트
uv run ruff check .     # 린트
```

| 경로 | 내용 |
|---|---|
| `src/recipe_chatbot/graph.py` | 그래프 조립: 시작 → 수퍼바이저 ⇄ 에이전트 → 검증 → 응답 → 끝 |
| `src/recipe_chatbot/nodes.py` | 노드: 수퍼바이저, 재료·레시피·영양 에이전트, 결정 모델, 검증, 응답 |
| `src/recipe_chatbot/selection.py` | 입력 게이트와 결정 모델의 규칙 단계 |
| `src/recipe_chatbot/models.py` | 모델 라우터 `(베이스 모델, 어댑터)`, vLLM·Ollama 공용 클라이언트, 가짜 모델 |
| `src/recipe_chatbot/tools.py` | 코드 도구: 재료 목록 갱신, 궁합 경고, 레시피 검색, 영양 계산 |
| `src/recipe_chatbot/domain/` | v1에서 옮긴 재료명 정규화, 성분 카테고리, 궁합 검사 |
| `config/models.example.toml` | 에이전트별 모델 설정 예시 |

## v1에서 달라지는 점

| | v1 | v2 |
|---|---|---|
| 흐름 제어 | 분류기 하나가 경로를 정하는 고정 그래프 | 수퍼바이저가 에이전트에 일을 맡기는 동적 구조 |
| 후보 선택 | 분류 프롬프트로 번호 판정 | 입력 게이트(코드)와 결정 모델(규칙 → 작은 모델 → 되묻기) |
| 모델 | 외부 API (Claude → Gemini) | 로컬 모델 중심: 큰 모델 1개 + 작은 모델 1개 + 역할별 어댑터 |
| 영양 정보 | 미사용 | 식품영양성분 DB로 코드가 계산 |
| 재시도 | 없음 | 검증 노드를 통과하지 못하면 다시 시도 |
| 레시피 변형(아렌지) | 기획만 함 | 구현 예정 |
| 완성 사진 | 없음 | 외부 이미지 모델로 생성 (검토 중) |

## 구조

![레시피 챗봇 v2 요청 흐름](docs/images/architecture-v2-editorial.png)

| 번호 | 노드명 | 역할 | 예상 모델 | 입력 | 출력 | 다음 노드 |
|---|---|---|---|---|---|---|
| 시작 | 사용자 질문 | 재료, 조건, 수정 요청을 채팅으로 받는다 | 없음 | 채팅 메시지 | 사용자 질문 | 0 |
| 0 | 입력 게이트 | 후보 목록이 떠 있고 발화가 선택처럼 보이면 결정 모델로, 아니면 수퍼바이저로 보낸다 | 없음 (코드 규칙) | 사용자 발화, 후보 목록 | 경로 | 선택 → D, 그 외 → 1 |
| 1 | 수퍼바이저 | 의도를 파악해 에이전트를 배정하고 결과를 종합한다. 게이트가 놓친 선택도 결정 모델로 넘기고, 모호하면 되묻는다 | 큰 모델 + 수퍼바이저 어댑터 | 사용자 질문, 대화 상태 | 에이전트 호출, 종합 결과 | A·B·C·D 호출 후 2 |
| A | 재료 에이전트 | 재료 추출·정규화, 궁합 검사, 요리 가능 여부 판정 | 작은 모델 | 사용자 발화, 재료 목록 | 갱신된 재료 목록, 궁합 경고 | 1로 반환 |
| B | 레시피 에이전트 | RAG·이름 검색, 레시피 후보 생성·확정·변형 | 큰 모델 + 레시피 어댑터 | 재료 목록, 사용자 조건 | 정형 레시피 또는 후보 목록 | 1로 반환 |
| C | 영양 에이전트 | 영양 계산, 대체 재료 추천 | 작은 모델 + 계산 코드 | 정형 레시피, 영양 DB | 영양 정보 | 1로 반환 |
| D | 결정 모델 | 고른 후보를 정한다. 규칙으로 먼저 정하고, 못 정하면 작은 모델, 그래도 모르면 되묻는다 | 규칙 + 작은 모델 | 사용자 발화, 후보 목록 | 확정 레시피 또는 되묻기 | 확정 → 2, 되묻기 → 3, 선택 아님 → 1 |
| 2 | 검증 노드 | 영양 목표, 궁합, 알레르기 조건을 검사한다. 후보 목록이면 조건을 어기는 후보를 뺀다 | 없음 (코드) | 종합 결과 | 통과 또는 미달 | 통과 → 3, 미달 → 1 |
| 3 | 응답 | 레시피 또는 번호 붙은 후보 목록을 먼저 스트리밍으로 보낸다 | 없음 (코드) | 검증된 결과 | SSE 응답 | 끝 (사진 요청 시 P) |
| P | 완성 사진 (선택) | 완성 모습 이미지를 만들고 AI 생성 예시로 표시한다 | 외부 이미지 모델 | 정형 레시피 | 이미지 | 끝 |

## 문서

- [v2 아키텍처 설계 기록](docs/architecture/recipe-chatbot-v2-architecture.md): v1 분석, 검토한 초안 A~D, 결정과 이유, 모델 전략, 진행 순서
- [작업 계획](docs/roadmap.md): 벤치마크, 기반 코드, 통합 검증, 어댑터 학습 순서와 병행 구간
- [업무 플로우](docs/workflow.md): 업무별 상태, 담당, 시작·종료 날짜, 결과를 기록하는 진행 관리 문서
- [평가 데이터셋](data/eval/README.md): 벤치마크용 역할별 평가 데이터의 형식, 출처, 정답 규칙

## 그림 관리

| 문서 | 그림 형식 |
|---|---|
| 확정 그림 (README, 설계 기록, 작업 계획, 업무 플로우) | `docs/images/*.png`. 원본은 `docs/diagrams/*.spec.json` |
| 검토 중인 초안 | Mermaid 코드 블록 또는 화이트보드. 확정되면 명세로 옮겨 PNG로 바꿈 |

확정 그림은 명세 파일 하나에서 만듭니다. 그림에는 번호와 노드명만 넣고, 역할·모델·입출력은 표나 본문에 씁니다. 한 줄 흐름은 기본 레이아웃, 갈라지고 합쳐지는 흐름은 격자 레이아웃(`"layout": "grid"`)을 씁니다.

모든 그림 오른쪽 아래에 `버전 · 작성 일시`가 붙습니다. 명세 내용이 바뀔 때만 버전이 오르고 일시가 바뀌므로, 문서 내용과 그림의 버전·일시를 비교하면 그림이 최신인지 알 수 있습니다. 내용이 같으면 다시 렌더링해도 그대로입니다.

- 명세: [docs/diagrams/architecture-v2.spec.json](docs/diagrams/architecture-v2.spec.json)
- 생성된 표: [docs/diagrams/architecture-v2.nodes.md](docs/diagrams/architecture-v2.nodes.md)
- 생성 도구: 작성자 로컬의 `spec-figure` 스킬 (`figure.py`). Python 3와 Chrome 헤드리스로 SVG와 2배 해상도 PNG를 만듭니다.

```bash
python3 ~/.claude/skills/spec-figure/scripts/figure.py docs/diagrams/architecture-v2.spec.json --style editorial --out docs/images
```

```bash
for f in docs/diagrams/*.spec.json; do python3 ~/.claude/skills/spec-figure/scripts/figure.py "$f" --out docs/images; done
```

명세를 고친 뒤에는 그림을 다시 만들고, README의 표도 새로 생성된 `architecture-v2.nodes.md` 내용으로 바꿉니다.
