"""수집 상태표(SQLite)와 원본·파싱 결과 보관.

단계마다 따로 실행하고, 끊겨도 이어서 할 수 있게 레시피마다 어느 단계까지 왔는지 기록한다.

    listed  → 목록에서 id만 찾음
    fetched → 원본을 저장함 (raw/)
    parsed  → 공통 형식 JSON을 저장함 (parsed/)
    failed  → 받기나 파싱에 실패함 (error에 이유)
    skipped → 원본은 있지만 레시피가 아님 (재료·순서 없음 등)

원본을 보관하므로 파싱·정제 규칙을 바꿔도 사이트에 다시 요청하지 않는다.
폴더 전체(`data/ingest/`)는 git에서 무시한다.
"""

import gzip
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from .records import SourceRecipe

STATUSES = ("listed", "fetched", "parsed", "failed", "skipped")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    status     TEXT NOT NULL,
    found_by   TEXT,
    error      TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
)
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "manifest.sqlite")
        self.db.execute(_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---- 상태표 ----
    def add_listed(self, source: str, ids: Iterable[str], found_by: str = "") -> int:
        """처음 보는 id만 listed로 넣고, 새로 넣은 개수를 돌려준다."""
        cur = self.db.executemany(
            "INSERT OR IGNORE INTO items (source, source_id, status, found_by, updated_at) "
            "VALUES (?, ?, 'listed', ?, ?)",
            [(source, i, found_by, _now()) for i in ids],
        )
        self.db.commit()
        return cur.rowcount

    def mark(self, source: str, source_id: str, status: str, error: str | None = None) -> None:
        assert status in STATUSES, status
        self.db.execute(
            "INSERT INTO items (source, source_id, status, error, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(source, source_id) DO UPDATE SET status=excluded.status, error=excluded.error, "
            "updated_at=excluded.updated_at",
            (source, source_id, status, error, _now()),
        )
        self.db.commit()

    def ids(self, source: str, status: str, limit: int | None = None) -> list[str]:
        sql = "SELECT source_id FROM items WHERE source=? AND status=? ORDER BY updated_at, source_id"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [r[0] for r in self.db.execute(sql, (source, status))]

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for source, status, n in self.db.execute("SELECT source, status, COUNT(*) FROM items GROUP BY 1, 2"):
            out.setdefault(source, {})[status] = n
        return out

    # ---- 원본 ----
    def raw_path(self, source: str, source_id: str, ext: str) -> Path:
        return self.root / "raw" / source / f"{source_id}.{ext}.gz"

    def save_raw(self, source: str, source_id: str, ext: str, data: bytes) -> Path:
        path = self.raw_path(source, source_id, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(data))
        return path

    def load_raw(self, source: str, source_id: str, ext: str) -> bytes:
        return gzip.decompress(self.raw_path(source, source_id, ext).read_bytes())

    # ---- 파싱 결과 ----
    def parsed_path(self, source: str, source_id: str) -> Path:
        return self.root / "parsed" / source / f"{source_id}.json"

    def save_parsed(self, recipe: SourceRecipe) -> Path:
        path = self.parsed_path(recipe.source, recipe.source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
        return path

    def iter_parsed(self, source: str | None = None) -> Iterable[SourceRecipe]:
        base = self.root / "parsed"
        pattern = f"{source}/*.json" if source else "*/*.json"
        for path in sorted(base.glob(pattern)):
            yield SourceRecipe.model_validate_json(path.read_text(encoding="utf-8"))
