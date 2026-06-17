"""Tests for Phase 4: MCP tool implementations."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "simple_python"


def _build_full_index(tmp_path: Path):
    """Build a complete index (graph + topology + search) from fixture."""
    target = tmp_path / "simple_python"
    shutil.copytree(FIXTURE, target)

    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema
    from orgraph.search.index import SearchIndex
    from orgraph.topology.cluster import build_nx_graph_from_result, cluster
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.serialise import save_communities, save_topology
    from orgraph.topology.topology import build_topology_map

    orgraph_dir = target / ".orgraph"
    orgraph_dir.mkdir(parents=True, exist_ok=True)

    result = TreeSitterExtractor(repo_path=target).run()

    db_path = orgraph_dir / "graph.kuzu"
    db = OrgraphDB(db_path)
    create_schema(db)
    builder = GraphBuilder(db=db, repo_path=target)
    builder.ingest(result)

    ctx = build_repo_context(result, target)
    topology = build_topology_map(ctx)
    G = build_nx_graph_from_result(result)
    communities = cluster(G)
    save_topology(topology, orgraph_dir)
    save_communities(communities, orgraph_dir)

    idx = SearchIndex.build(target)

    return db, idx, topology, communities, target


def _get_tools(db, idx, topology, communities, repo_path):
    from fastmcp import FastMCP
    from orgraph.mcp.tools import register_tools
    mcp = FastMCP("orgraph-test")
    return register_tools(mcp, db, idx, topology, communities, repo_path)


@pytest.fixture(scope="module")
def full_index(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mcp_index")
    return _build_full_index(tmp)


# ── all tools registered ───────────────────────────────────────────────────

def test_all_tools_registered(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    assert set(tools.keys()) == {"search", "trace", "get_context", "find_entry_points", "get_dependencies", "reindex"}


# ── search tool ────────────────────────────────────────────────────────────

def test_search_tool_returns_results(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    results = tools["search"](query="authenticate user", top_k=3)
    assert isinstance(results, list)
    assert len(results) > 0
    assert "file" in results[0]
    assert "score" in results[0]
    assert "snippet" in results[0]


def test_search_tool_no_index_returns_error(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, None, topology, communities, repo_path)
    results = tools["search"](query="anything")
    assert results[0].get("error")


# ── trace tool ─────────────────────────────────────────────────────────────

def test_trace_tool_found(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["trace"](symbol="authenticate", direction="callees", depth=2)
    assert isinstance(result, dict)
    assert "root" in result
    assert "chain" in result
    assert isinstance(result["chain"], list)


def test_trace_tool_unknown_symbol(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["trace"](symbol="__nonexistent_xyz__", direction="callees", depth=1)
    assert result["found"] is False


def test_trace_returns_callers(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["trace"](symbol="authenticate", direction="callers", depth=2)
    assert isinstance(result, dict)
    assert "chain" in result


# ── get_context tool ────────────────────────────────────────────────────────

def test_get_context_by_symbol(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["get_context"](file_or_symbol="authenticate")
    assert isinstance(result, dict)
    assert "found" in result


def test_get_context_by_file(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["get_context"](file_or_symbol="auth.py")
    assert isinstance(result, dict)
    assert "found" in result


# ── find_entry_points tool ─────────────────────────────────────────────────

def test_find_entry_points_returns_list(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["find_entry_points"](kind="all")
    assert isinstance(result, list)


def test_find_entry_points_each_has_kind(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["find_entry_points"](kind="all")
    for item in result[:5]:
        if "error" not in item:
            assert "kind" in item


# ── get_dependencies tool ──────────────────────────────────────────────────

def test_get_dependencies_returns_dict(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["get_dependencies"](file_path="handlers.py", direction="imports", depth=2)
    assert isinstance(result, dict)
    assert "deps" in result
    assert "dep_count" in result


def test_get_dependencies_imported_by(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["get_dependencies"](file_path="models.py", direction="imported_by", depth=1)
    assert isinstance(result, dict)
    assert "deps" in result
