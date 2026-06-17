"""Agent detection and MCP config targets for orgraph install."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_HOME = Path.home()

Action = Literal["created", "updated", "unchanged", "not-found", "removed", "error"]
Mode = Literal["install", "uninstall"]

# orgraph serve uses "." so MCP client cwd (workspace root) is the repo
_MCP_ENTRY: dict[str, object] = {
    "command": "uvx",
    "args": ["--from", "orgraph-mcp", "orgraph", "serve", "."],
    "type": "stdio",
}

# VS Code uses "servers" key and slightly different shape
_VSCODE_MCP_ENTRY: dict[str, object] = {
    "command": "uvx",
    "args": ["--from", "orgraph-mcp", "orgraph", "serve", "."],
    "type": "stdio",
}

CLAUDE_MD_BLOCK = """\
<!-- ORGRAPH_START -->
## orgraph — Codebase Knowledge Graph

An `orgraph` MCP server is running with 5 tools:
- `search(query, top_k)` — hybrid BM25+semantic search over code chunks
- `trace(symbol, direction, depth)` — follow call chains forward (callees) or backward (callers)
- `get_context(file_or_symbol)` — topology cluster + community placement, call depth, indegree
- `find_entry_points(kind)` — HTTP handlers and entry surfaces; kind = "all" | "http" | "topology"
- `get_dependencies(file_path, direction, depth)` — import + call dependency tree

### Workflow
1. Start with `search` to find relevant code by description.
2. Use `trace` to follow a function's call chain — don't read files to discover callers/callees.
3. Use `get_context` to understand where a file/symbol fits architecturally before editing.
4. Use `find_entry_points` to map the API surface of an unfamiliar codebase.
5. Use `get_dependencies` to understand what a file pulls in before refactoring it.
<!-- ORGRAPH_END -->
"""

ORGRAPH_START = "<!-- ORGRAPH_START -->"
ORGRAPH_END = "<!-- ORGRAPH_END -->"


def _vscode_mcp_path() -> Path:
    if sys.platform == "darwin":
        base = _HOME / "Library" / "Application Support" / "Code" / "User"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", _HOME)) / "Code" / "User"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", _HOME / ".config")) / "Code" / "User"
    return base / "mcp.json"


@dataclass(frozen=True)
class McpConfig:
    path: Path
    key: str
    entry: dict[str, object]


@dataclass(frozen=True)
class AgentTarget:
    id: str
    display_name: str
    binary: str | None
    config_dir: Path | None
    mcp: McpConfig | None
    instructions_path: Path | None  # None = not supported


def is_detected(agent: AgentTarget) -> bool:
    if agent.binary and shutil.which(agent.binary):
        return True
    return bool(agent.config_dir and agent.config_dir.exists())


AGENTS: list[AgentTarget] = [
    AgentTarget(
        id="claude",
        display_name="Claude Code",
        binary="claude",
        config_dir=_HOME / ".claude",
        mcp=McpConfig(_HOME / ".claude.json", "mcpServers", _MCP_ENTRY),
        instructions_path=_HOME / ".claude" / "CLAUDE.md",
    ),
    AgentTarget(
        id="cursor",
        display_name="Cursor",
        binary="cursor",
        config_dir=_HOME / ".cursor",
        mcp=McpConfig(_HOME / ".cursor" / "mcp.json", "mcpServers", _MCP_ENTRY),
        instructions_path=None,
    ),
    AgentTarget(
        id="codex",
        display_name="Codex",
        binary="codex",
        config_dir=_HOME / ".codex",
        mcp=McpConfig(_HOME / ".codex" / "config.toml", "mcp_servers", _MCP_ENTRY),
        instructions_path=_HOME / ".codex" / "AGENTS.md",
    ),
    AgentTarget(
        id="vscode",
        display_name="VS Code",
        binary="code",
        config_dir=None,
        mcp=McpConfig(_vscode_mcp_path(), "servers", _VSCODE_MCP_ENTRY),
        instructions_path=None,
    ),
    AgentTarget(
        id="gemini",
        display_name="Gemini CLI",
        binary="gemini",
        config_dir=_HOME / ".gemini",
        mcp=McpConfig(_HOME / ".gemini" / "settings.json", "mcpServers", _MCP_ENTRY),
        instructions_path=_HOME / ".gemini" / "GEMINI.md",
    ),
    AgentTarget(
        id="opencode",
        display_name="Opencode",
        binary="opencode",
        config_dir=_HOME / ".config" / "opencode",
        mcp=McpConfig(_HOME / ".config" / "opencode" / "opencode.json", "mcp", _MCP_ENTRY),
        instructions_path=_HOME / ".config" / "opencode" / "AGENTS.md",
    ),
]
