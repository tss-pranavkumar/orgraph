"""Semble-backed search index (P3 implementation)."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class SearchIndex:
    def __init__(self, _index: Any) -> None:
        self._index = _index

    @classmethod
    def build(cls, repo_path: Path) -> "SearchIndex":
        from semble import ContentType, SembleIndex
        idx = SembleIndex.from_path(repo_path, content=[ContentType.CODE])
        search_dir = repo_path / ".orgraph" / "search"
        search_dir.mkdir(parents=True, exist_ok=True)
        idx.save(search_dir)
        return cls(idx)

    @classmethod
    def load(cls, repo_path: Path) -> "SearchIndex | None":
        search_dir = repo_path / ".orgraph" / "search"
        if not search_dir.exists():
            return None
        try:
            from semble import SembleIndex
            return cls(SembleIndex.load_from_disk(search_dir))
        except Exception:
            return None

    def search(self, query: str, top_k: int = 10, **kwargs):
        return self._index.search(query, top_k=top_k, **kwargs)

    def find_related(self, result, top_k: int = 5):
        return self._index.find_related(result, top_k=top_k)
