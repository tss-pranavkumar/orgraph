"""Thin wrapper around Kuzu's native Python API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import kuzu


class OrgraphDB:
    """Manages a Kuzu database for a single indexed repo."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        # Kuzu creates the db directory itself — only ensure the parent exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self.db_path))
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
