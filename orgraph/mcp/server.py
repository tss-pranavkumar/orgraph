"""MCP server — FastMCP stdio transport, starts immediately and loads state lazily.

The MCP handshake completes in <1s. If the repo isn't indexed yet, indexing
runs on the first tool call — no connection timeout.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path


def start_server(repo_path: Path) -> None:
    """Start MCP stdio server immediately; load/index state in background."""
    from fastmcp import FastMCP

    from orgraph.mcp.tools import State, register_tools

    orgraph_dir = repo_path / ".orgraph"

    # Start with empty state — tools handle None gracefully and return
    # "indexing in progress" messages until state is populated.
    state = State(db=None, idx=None, topology=None, communities=None, repo_path=repo_path)
    state.rebuild_lookups()

    mcp = FastMCP(
        "orgraph",
        instructions=(
            f"Codebase knowledge graph for {repo_path.name}. "
            "Use `search` to find relevant code, `trace` to follow call chains, "
            "`get_context` to understand where a file or symbol fits architecturally, "
            "`find_entry_points` to see HTTP handlers and entry surfaces, "
            "`get_dependencies` to map import/call dependencies."
        ),
    )

    register_tools(mcp, state.db, state.idx, state.topology, state.communities, repo_path)

    # Load (and auto-index if needed) in background so stdio starts immediately
    _ready = threading.Event()

    def _load() -> None:
        try:
            _ensure_indexed(repo_path, orgraph_dir)
            _load_into_state(state, repo_path, orgraph_dir)
        except Exception as exc:
            print(f"orgraph: background load failed: {exc}", file=sys.stderr)
        finally:
            _ready.set()

    threading.Thread(target=_load, daemon=True).start()

    mcp.run(transport="stdio")


def _ensure_indexed(repo_path: Path, orgraph_dir: Path) -> None:
    """Run orgraph index if graph.kuzu doesn't exist yet."""
    db_path = orgraph_dir / "graph.kuzu"

    # Migrate old single-file kuzu format
    if db_path.exists() and not db_path.is_dir():
        db_path.unlink()
        print("orgraph: removed stale single-file graph.kuzu (format migration)", file=sys.stderr)

    if db_path.exists():
        return

    print(f"orgraph: no index found for {repo_path.name} — indexing now…", file=sys.stderr)
    from click.testing import CliRunner
    from orgraph.cli import index
    result = CliRunner().invoke(index, [str(repo_path)])
    if result.exit_code != 0:
        print(f"orgraph: auto-index failed\n{result.output}", file=sys.stderr)


def _load_into_state(state, repo_path: Path, orgraph_dir: Path) -> None:
    """Load DB, search index, topology, communities into state in-place."""
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.search.index import SearchIndex
    from orgraph.topology.serialise import load_communities, load_topology

    db_path = orgraph_dir / "graph.kuzu"
    if not db_path.exists():
        return  # indexing failed — tools will return "not ready" messages

    state.db = OrgraphDB(db_path)
    state.idx = SearchIndex.load(repo_path)
    state.topology = load_topology(orgraph_dir)
    state.communities = load_communities(orgraph_dir)
    state.rebuild_lookups()
