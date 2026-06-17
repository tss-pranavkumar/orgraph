"""Thin wrapper around Kuzu's native Python API."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import kuzu


class OrgraphDB:
    """Manages a Kuzu database for a single indexed repo."""

    def __init__(self, db_path: str | Path, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self.db_path), read_only=read_only)
        self._conn = kuzu.Connection(self._db)

    def execute(self, query: str, params: dict[str, Any] | None = None) -> kuzu.QueryResult:
        if params:
            return self._conn.execute(query, parameters=params)
        return self._conn.execute(query)

    def execute_many(self, queries: list[str]) -> None:
        for q in queries:
            self._conn.execute(q)

    def query_to_dicts(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        result = self.execute(query, params)
        rows: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            cols = result.get_column_names()
            rows.append(dict(zip(cols, row)))
        return rows

    def close(self) -> None:
        del self._conn
        del self._db


@contextmanager
def open_db_readonly(db_path: Path):
    """Open a Kuzu DB for read-only queries alongside a running server.

    Kuzu acquires an exclusive lock even with read_only=True, so we copy the
    DB directory to a temp location and open that instead.
    """
    tmp = tempfile.mkdtemp(prefix="orgraph_ro_")
    tmp_db = Path(tmp) / "graph.kuzu"
    try:
        shutil.copytree(str(db_path), str(tmp_db))
        db = OrgraphDB(tmp_db)
        try:
            yield db
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
