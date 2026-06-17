"""Tests for Phase 3: semble-backed search index."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "simple_python"


def _build_index(tmp_path: Path):
    """Build a fresh SearchIndex from the fixture in a clean temp dir."""
    # Copy to tmp so .orgraph/ is written there, not in the fixture
    target = tmp_path / "simple_python"
    shutil.copytree(FIXTURE, target)
    from orgraph.search.index import SearchIndex
    return SearchIndex.build(target), target


def test_build_returns_search_index(tmp_path):
    idx, _ = _build_index(tmp_path)
    assert idx is not None


def test_search_dir_created(tmp_path):
    _, target = _build_index(tmp_path)
    search_dir = target / ".orgraph" / "search"
    assert search_dir.exists(), "Search index directory should be created"


def test_search_returns_results(tmp_path):
    idx, _ = _build_index(tmp_path)
    results = idx.search("authenticate user", top_k=5)
    assert len(results) > 0


def test_search_results_have_expected_shape(tmp_path):
    idx, _ = _build_index(tmp_path)
    results = idx.search("user model", top_k=3)
    assert len(results) > 0
    r = results[0]
    assert hasattr(r, "chunk")
    assert hasattr(r, "score")
    assert isinstance(r.score, float)
    chunk = r.chunk
    assert chunk.file_path
    assert isinstance(chunk.start_line, int)
    assert isinstance(chunk.end_line, int)
    assert chunk.content


def test_search_relevant_symbol_in_top3(tmp_path):
    idx, _ = _build_index(tmp_path)
    results = idx.search("authenticate", top_k=3)
    paths = [r.chunk.file_path for r in results]
    assert any("auth" in p for p in paths), (
        f"Expected auth.py in top-3 results for 'authenticate', got: {paths}"
    )


def test_load_from_disk(tmp_path):
    idx, target = _build_index(tmp_path)
    from orgraph.search.index import SearchIndex
    loaded = SearchIndex.load(target)
    assert loaded is not None


def test_load_returns_none_when_missing(tmp_path):
    from orgraph.search.index import SearchIndex
    result = SearchIndex.load(tmp_path / "nonexistent")
    assert result is None


def test_load_gives_same_results(tmp_path):
    idx, target = _build_index(tmp_path)
    from orgraph.search.index import SearchIndex
    loaded = SearchIndex.load(target)
    assert loaded is not None
    r1 = idx.search("user authentication", top_k=3)
    r2 = loaded.search("user authentication", top_k=3)
    paths1 = [r.chunk.file_path for r in r1]
    paths2 = [r.chunk.file_path for r in r2]
    assert paths1 == paths2, "Loaded index should return same results"


def test_find_related(tmp_path):
    idx, _ = _build_index(tmp_path)
    results = idx.search("authenticate", top_k=1)
    assert results
    related = idx.find_related(results[0], top_k=3)
    assert isinstance(related, list)
