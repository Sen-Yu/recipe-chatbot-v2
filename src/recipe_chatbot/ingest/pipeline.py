"""수집 단계 실행: 찾기(discover) → 받기(fetch) → 파싱(parse).

단계마다 따로 실행한다. 상태표를 보고 남은 것만 처리하므로 끊겨도 다시 실행하면 이어진다.
받기는 사이트에 요청하는 유일한 단계라, 출처의 대기 시간을 지키고 연속 실패하면 멈춘다.
"""

import time
from dataclasses import dataclass, field

import httpx

from .sources.base import USER_AGENT, Sleep, Source
from .store import Store

MAX_CONSECUTIVE_FAILS = 5  # 연속으로 이만큼 실패하면 차단으로 보고 멈춘다


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=15.0,
        follow_redirects=True,
        transport=transport,
    )


@dataclass
class StageResult:
    done: int = 0
    failed: int = 0
    skipped: int = 0
    stopped: str = ""
    errors: list[str] = field(default_factory=list)


def discover(source: Source, client: httpx.Client, store: Store, sleep: Sleep = time.sleep, **opts) -> int:
    return source.discover(client, store, sleep, **opts)


def fetch(
    source: Source,
    client: httpx.Client,
    store: Store,
    limit: int | None = None,
    sleep: Sleep = time.sleep,
) -> StageResult:
    result, streak = StageResult(), 0
    for sid in store.ids_interleaved(source.name, "listed", limit):
        try:
            raw = source.fetch(client, sid)
        except httpx.HTTPError as e:
            store.mark(source.name, sid, "failed", f"fetch: {e}")
            result.failed += 1
            result.errors.append(f"{sid}: {e}")
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILS:
                result.stopped = f"연속 {streak}회 실패, 차단 가능성이 있어 멈춤"
                break
        else:
            store.save_raw(source.name, sid, source.raw_ext, raw)
            store.mark(source.name, sid, "fetched")
            result.done += 1
            streak = 0
        sleep(source.fetch_delay)
    return result


def parse(source: Source, store: Store, reparse: bool = False) -> StageResult:
    """받은 원본을 공통 형식으로 바꾼다. reparse면 이미 파싱한 것도 원본에서 다시 만든다."""
    result = StageResult()
    targets = store.ids(source.name, "fetched")
    if reparse:
        targets += store.ids(source.name, "parsed") + store.ids(source.name, "skipped")
    for sid in targets:
        try:
            recipe = source.parse(sid, store.load_raw(source.name, sid, source.raw_ext))
        except Exception as e:  # 원본 하나가 깨져도 나머지는 계속
            store.mark(source.name, sid, "failed", f"parse: {e}")
            result.failed += 1
            result.errors.append(f"{sid}: {e}")
            continue
        if recipe is None:
            store.mark(source.name, sid, "skipped", "레시피 정보 없음")
            result.skipped += 1
            continue
        store.save_parsed(recipe)
        store.mark(source.name, sid, "parsed")
        result.done += 1
    return result
