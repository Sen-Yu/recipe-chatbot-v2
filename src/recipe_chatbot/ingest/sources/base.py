"""출처 어댑터의 공통 틀.

어댑터는 세 가지만 안다: 어떻게 id를 찾는지(discover), 원본을 어떻게 받는지(fetch),
원본을 공통 형식으로 어떻게 바꾸는지(parse). 대기 시간과 실패 처리는 pipeline이 맡는다.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable

import httpx

from ..records import SourceRecipe, Usage
from ..store import Store

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 (personal-research-script)"
)

Sleep = Callable[[float], None]


class Source(ABC):
    name: str
    raw_ext: str
    usage: Usage
    list_delay: float = 0.0  # 목록 요청 사이 대기 (초)
    fetch_delay: float = 0.0  # 원본 요청 사이 대기 (초)

    @abstractmethod
    def discover(self, client: httpx.Client, store: Store, sleep: Sleep, **opts) -> int:
        """새 id를 찾아 상태표에 넣고, 새로 찾은 개수를 돌려준다.

        API처럼 목록에 원본이 함께 오는 출처는 여기서 원본까지 저장하고 fetched로 표시한다.
        """

    def fetch(self, client: httpx.Client, source_id: str) -> bytes:
        """원본 하나를 받는다. 목록에 원본이 함께 오는 출처는 쓰지 않는다."""
        raise NotImplementedError(f"{self.name}: 원본은 discover에서 함께 저장합니다")

    @abstractmethod
    def parse(self, source_id: str, raw: bytes) -> SourceRecipe | None:
        """원본을 공통 형식으로 바꾼다. 레시피가 아니면 None."""
