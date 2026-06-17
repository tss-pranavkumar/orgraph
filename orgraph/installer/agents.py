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

def _resolve_orgraph_bin() -> str:
    """Return the orgraph binary path that is currently running.

    Using sys.argv[0] means the installed MCP entry points to whatever
    orgraph the user actually has — uvx cache, venv, system install —
    instead of re-fetching from PyPI at server start time.
    """
    try:
        p = Path(sys.argv[0]).resolve()
        if p.exists():
            return str(p)
    except Exception:
        pass
    # Fallback: let the shell find it
    found = shutil.which("orgraph")
    return found or "orgraph"


def get_mcp_entry() -> dict[str, object]:
    """Build the global stdio MCP entry using the currently-running orgraph binary.

    No repo path in args — tools accept `repo` per call (like semble).
    """
    return {"command": _resolve_orgraph_bin(), "args": ["serve"], "type": "stdio"}


def get_opencode_mcp_entry() -> dict[str, object]:
    """Build the global Opencode MCP entry using the currently-running orgraph binary."""
    bin_ = _resolve_orgraph_bin()
    return {"command": [bin_, "serve"], "type": "local", "enabled": True}


# Keep module-level names for import compatibility — evaluated lazily at install time
_MCP_ENTRY: dict[str, object] = {"command": "uvx", "args": ["--from", "orgraph-mcp", "orgraph", "serve", "."], "type": "stdio"}
_OPENCODE_MCP_ENTRY: dict[str, object] = {"command": ["uvx", "--from", "orgraph-mcp", "orgraph", "serve", "."], "type": "local", "enabled": True}

_ORGRAPH_TOOLS_TABLE = """\
Always pass `repo` as the absolute path to the current project (the git root) with every call.

Use these tools instead of grep/find/Read:

| Instead of | Use |
|---|---|
| `grep -r "foo"` or `find` | `search(query, repo)` — semantic + keyword search |
| Reading files to trace calls | `trace(symbol, repo)` — follows call chains in the graph |
| Guessing what a file does | `get_context(file_or_symbol, repo)` — cluster, depth, indegree |
| Listing API endpoints | `find_entry_points(kind="http", repo)` — HTTP handlers, CLI entry points |
| Checking imports manually | `get_dependencies(file_path, repo)` — import/call dependency tree |

When to use each:
- **Where is X implemented?** → `search`
- **What does this function call / what calls it?** → `trace`
- **What is this file's role before I edit it?** → `get_context`
- **What are all the HTTP endpoints?** → `find_entry_points(kind="http")`
- **What does this file import?** → `get_dependencies`\
"""

# Claude Code: tools are deferred — must call ToolSearch to load schemas first
CLAUDE_MD_BLOCK = """\
<!-- ORGRAPH_START -->
## orgraph — Codebase Knowledge Graph

orgraph is a global MCP server with a pre-built call graph, topology clusters, and hybrid search index for any indexed repo. **Use it instead of grep, find, Bash, or Read for any question about how the codebase works.**

### Step 1 — Load tools at the start of every session

```
ToolSearch: select:mcp__orgraph__search,mcp__orgraph__trace,mcp__orgraph__get_context,mcp__orgraph__find_entry_points,mcp__orgraph__get_dependencies
```

### Step 2 — Use orgraph tools, not grep/find/Read

""" + _ORGRAPH_TOOLS_TABLE + """
<!-- ORGRAPH_END -->
"""

# Codex / Gemini CLI / Opencode: MCP tools load automatically, no ToolSearch needed
AGENTS_MD_BLOCK = """\
<!-- ORGRAPH_START -->
## orgraph — Codebase Knowledge Graph

orgraph is a global MCP server with a pre-built call graph, topology clusters, and hybrid search index for any indexed repo. **Use it instead of grep, find, Bash, or Read for any question about how the codebase works.**

The orgraph MCP tools are available as: `orgraph__search`, `orgraph__trace`, `orgraph__get_context`, `orgraph__find_entry_points`, `orgraph__get_dependencies`.

""" + _ORGRAPH_TOOLS_TABLE + """
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


def _opencode_mcp_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) / "opencode" if xdg else _HOME / ".config" / "opencode"
    jsonc = base / "opencode.jsonc"
    json_ = base / "opencode.json"
    return jsonc if jsonc.exists() else (json_ if json_.exists() else jsonc)


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
        mcp=McpConfig(_vscode_mcp_path(), "servers", _MCP_ENTRY),
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
        mcp=McpConfig(_opencode_mcp_path(), "mcp", _OPENCODE_MCP_ENTRY),
        instructions_path=_HOME / ".config" / "opencode" / "AGENTS.md",
    ),
]
