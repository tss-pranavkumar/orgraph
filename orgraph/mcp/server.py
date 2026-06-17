"""MCP server — FastMCP stdio transport.

Starts immediately. State is loaded lazily per repo in background threads.
Supports both project-specific mode (repo path at startup) and global mode
(no startup path; each tool call passes `repo`).
"""
from __future__ import annotations

from pathlib import Path


def start_server(repo_path: Path | None = None) -> None:
    """Start MCP stdio server.

    repo_path: if provided, pre-warms the state cache for that repo.
               If None, server starts globally — callers pass `repo` per tool call.
    """
    import sys
    from fastmcp import FastMCP
    from orgraph.mcp.tools import register_tools

    instructions = (
        "Codebase knowledge graph"
        + (f" for {repo_path.name}" if repo_path else "")
        + ". Use `search` to find relevant code, `trace` to follow call chains, "
        "`get_context` to understand where a file or symbol fits architecturally, "
        "`find_entry_points` to see HTTP handlers and entry surfaces, "
        "`get_dependencies` to map import/call dependencies."
        + (" Pass `repo` as the absolute path to your project with each tool call."
           if not repo_path else "")
    )

    mcp = FastMCP("orgraph", instructions=instructions)
    register_tools(mcp, startup_repo=repo_path)

    if repo_path:
        print(f"orgraph: serving {repo_path}", file=sys.stderr)

    mcp.run(transport="stdio")
