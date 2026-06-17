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
    return _build_full_index_from_path(target)


def _build_full_index_from_path(target: Path):
    """Build a complete index (graph + topology + search) from an existing repo path."""
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
    from orgraph.mcp import tools as mcp_tools
    from orgraph.mcp.tools import State, register_tools

    repo_path = repo_path.resolve()
    state = State(db=db, idx=idx, topology=topology, communities=communities, repo_path=repo_path)
    state.rebuild_lookups()
    mcp_tools._repo_states[str(repo_path)] = state
    mcp_tools._startup_repo = repo_path

    mcp = FastMCP("orgraph-test")
    return register_tools(mcp, startup_repo=repo_path)


@pytest.fixture(scope="module")
def full_index(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mcp_index")
    return _build_full_index(tmp)


# ── all tools registered ───────────────────────────────────────────────────

def test_all_tools_registered(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    assert set(tools.keys()) == {
        "search",
        "trace",
        "get_context",
        "list_symbols",
        "find_entry_points",
        "get_dependencies",
        "reindex",
    }


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


def test_search_snippet_allows_1000_chars(full_index):
    from dataclasses import dataclass

    db, _idx, topology, communities, repo_path = full_index

    @dataclass
    class Chunk:
        file_path: str
        start_line: int
        end_line: int
        content: str
        language: str

    @dataclass
    class SearchResult:
        chunk: Chunk
        score: float

    class FakeIndex:
        def search(self, query: str, top_k: int = 10):
            return [
                SearchResult(
                    chunk=Chunk(
                        file_path="long.py",
                        start_line=1,
                        end_line=80,
                        content="x" * 1500,
                        language="python",
                    ),
                    score=1.0,
                )
            ]

    tools = _get_tools(db, FakeIndex(), topology, communities, repo_path)
    results = tools["search"](query="anything")
    assert len(results[0]["snippet"]) == 1000


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


def test_trace_file_fallback_returns_candidates(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["trace"](symbol="auth.py", direction="callees", depth=1)
    assert result["found"] is False
    assert "candidates" in result
    assert any(c["name"] == "authenticate" for c in result["candidates"])


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
    assert "community_peers" in result


# ── list_symbols tool ───────────────────────────────────────────────────────

def test_list_symbols_returns_file_api_surface(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["list_symbols"](file_path="auth.py")
    assert isinstance(result, list)
    assert any(item["name"] == "authenticate" for item in result)
    assert result == sorted(result, key=lambda r: (r["line"], r["kind"], r["name"]))


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


def test_find_entry_points_tasks(tmp_path):
    target = tmp_path / "celery_project"
    target.mkdir()
    (target / "tasks.py").write_text(
        "def send_mail_task(user_id):\n"
        "    return user_id\n",
        encoding="utf-8",
    )
    (target / "refund.py").write_text(
        "from tasks import send_mail_task\n\n"
        "def initiate_refund_request(user_id):\n"
        "    send_mail_task.apply_async(args=[user_id])\n",
        encoding="utf-8",
    )
    db, idx, topology, communities, repo_path = _build_full_index_from_path(target)
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["find_entry_points"](kind="tasks")
    assert any(item["symbol"] == "send_mail_task" for item in result)


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


# ── truncation flags ─────────────────────────────────────────────────────────

def test_trace_reports_truncated_flag(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["trace"](symbol="authenticate")
    assert result["truncated"] is False  # tiny fixture never hits the cap


def test_get_dependencies_reports_truncated_flag(full_index):
    db, idx, topology, communities, repo_path = full_index
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["get_dependencies"](file_path="handlers.py")
    assert "truncated" in result


# ── trace ambiguity ──────────────────────────────────────────────────────────

def test_trace_disambiguates_duplicate_names(tmp_path):
    target = tmp_path / "dup"
    target.mkdir()
    (target / "a.py").write_text("def process():\n    return 1\n", encoding="utf-8")
    (target / "b.py").write_text("def process():\n    return 2\n", encoding="utf-8")
    db, idx, topology, communities, repo_path = _build_full_index_from_path(target)
    tools = _get_tools(db, idx, topology, communities, repo_path)

    result = tools["trace"](symbol="process", direction="callees", depth=1)
    assert result["found"] is True
    assert result["alternatives"], "expected the other 'process' as an alternative"
    assert "truncated" in result

    pinned = tools["trace"](symbol="process", direction="callees", depth=1, file="b.py")
    assert "b.py" in pinned["root_file"]


# ── reindex tool ─────────────────────────────────────────────────────────────

def _write_manifest(repo_path: Path) -> None:
    from orgraph.extract.manifest import Manifest
    m = Manifest(repo_path / ".orgraph")
    m.update(m.all_files(repo_path))
    m.save()


def _cross_file_calls(db, caller_frag: str, callee_frag: str) -> int:
    rows = db.query_to_dicts(
        "MATCH (caller:Function)-[:CALLS]->(callee) "
        "WHERE caller.path CONTAINS $a AND callee.path CONTAINS $b "
        "RETURN count(*) AS n",
        {"a": caller_frag, "b": callee_frag},
    )
    return rows[0]["n"] if rows else 0


def test_reindex_no_changes_is_noop(tmp_path):
    db, idx, topology, communities, repo_path = _build_full_index(tmp_path)
    _write_manifest(repo_path)
    tools = _get_tools(db, idx, topology, communities, repo_path)
    result = tools["reindex"](repo=str(repo_path))
    assert result["status"] == "up_to_date"


def test_reindex_rebuilds_and_preserves_cross_file_edges(tmp_path):
    db, idx, topology, communities, repo_path = _build_full_index(tmp_path)
    _write_manifest(repo_path)
    tools = _get_tools(db, idx, topology, communities, repo_path)

    before = _cross_file_calls(db, "handlers", "auth")
    assert before >= 1, "fixture should have handlers->auth cross-file calls"

    # Modify the CALLEE file. The old delta path dropped incoming handlers->auth
    # edges on this exact scenario; a full rebuild must preserve them.
    auth = repo_path / "auth.py"
    auth.write_text(auth.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")

    result = tools["reindex"](repo=str(repo_path))
    assert result["status"] == "updated"
    assert result["changed_files"] >= 1

    after = _cross_file_calls(db, "handlers", "auth")
    assert after >= before, "cross-file caller edges must survive reindex"

    # Topology must cover every indexed file, not just the changed one.
    from orgraph.topology.serialise import load_topology
    topo = load_topology(repo_path / ".orgraph")
    file_paths = {r["path"] for r in db.query_to_dicts("MATCH (f:File) RETURN f.path AS path")}
    for fp in file_paths:
        assert fp in topo.file_cluster_id, f"{fp} not assigned to a cluster after reindex"
