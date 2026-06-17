"""Tests for Kuzu schema and graph builder."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "simple_python"


def test_schema_creates_without_error(tmp_path):
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)  # should not raise
    db.close()


def test_schema_is_idempotent(tmp_path):
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    create_schema(db)  # second call with IF NOT EXISTS — should not raise
    db.close()


def test_builder_ingest_returns_counts(tmp_path):
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = TreeSitterExtractor(FIXTURE).run()
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    builder = GraphBuilder(db=db, repo_path=FIXTURE)
    nodes, edges = builder.ingest(result)
    assert nodes > 0, "Expected nodes written"
    db.close()


def test_builder_can_query_functions(tmp_path):
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = TreeSitterExtractor(FIXTURE).run()
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    GraphBuilder(db=db, repo_path=FIXTURE).ingest(result)

    rows = db.query_to_dicts("MATCH (f:Function) RETURN f.name AS name")
    names = [r["name"] for r in rows]
    assert len(names) > 0, "Expected queryable Function nodes"
    db.close()


def test_builder_can_query_files(tmp_path):
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = TreeSitterExtractor(FIXTURE).run()
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    GraphBuilder(db=db, repo_path=FIXTURE).ingest(result)

    rows = db.query_to_dicts("MATCH (f:File) RETURN f.name AS name")
    names = [r["name"] for r in rows]
    assert any("auth" in n or "handler" in n or "model" in n for n in names), \
        f"Expected fixture files, got: {names}"
    db.close()
