"""MCP server — FastMCP stdio transport, 5 tools.

Start with: orgraph serve <repo_path>
Add to Cursor: .cursor/mcp.json → {"mcpServers": {"orgraph": {"command": "orgraph", "args": ["serve", "."]}}}
Add to Claude CLI: .claude/settings.json → {"mcpServers": {"orgraph": {"command": "orgraph", "args": ["serve", "."]}}}
"""
from __future__ import annotations

import sys
from pathlib import Path


def start_server(repo_path: Path) -> None:
    """Load orgraph state and start an MCP stdio server."""
    from fastmcp import FastMCP

    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.mcp.tools import register_tools
    from orgraph.search.index import SearchIndex
    from orgraph.topology.serialise import load_communities, load_topology

    orgraph_dir = repo_path / ".orgraph"

    if not orgraph_dir.exists():
        print(
            f"error: {orgraph_dir} not found. Run `orgraph index {repo_path}` first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    db_path = orgraph_dir / "graph.kuzu"
    if not db_path.exists():
        print(
            f"error: graph.kuzu not found in {orgraph_dir}. Run `orgraph index` first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    db = OrgraphDB(db_path)
    idx = SearchIndex.load(repo_path)
    topology = load_topology(orgraph_dir)
    communities = load_communities(orgraph_dir)

    if idx is None:
        print("warning: search index not found — search tool disabled", file=sys.stderr)
    if topology is None:
        print("warning: topology.json not found — context/entry-point tools degraded", file=sys.stderr)

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

    register_tools(mcp, db, idx, topology, communities, repo_path)

    mcp.run(transport="stdio")
