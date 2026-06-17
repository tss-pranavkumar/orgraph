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

CLAUDE_MD_BLOCK = """\
<!-- ORGRAPH_START -->
## orgraph — Codebase Knowledge Graph

An `orgraph` MCP server is registered globally. Its tools are deferred — load them at the
start of every session before doing any codebase exploration:

```
ToolSearch: select:mcp__orgraph__search,mcp__orgraph__trace,mcp__orgraph__get_context,mcp__orgraph__find_entry_points,mcp__orgraph__get_dependencies
```

Once loaded, prefer orgraph over grep/find/Read for any question about how the code works.
**Always pass `repo` as the absolute path to the current project** with every tool call.

- `mcp__orgraph__search(query, repo)` — hybrid BM25+semantic search over code chunks
- `mcp__orgraph__trace(symbol, repo, direction, depth)` — follow call chains forward (callees) or backward (callers)
- `mcp__orgraph__get_context(file_or_symbol, repo)` — topology cluster + community placement, call depth, indegree
- `mcp__orgraph__find_entry_points(kind, repo)` — HTTP handlers and entry surfaces; kind = "all" | "http" | "topology"
- `mcp__orgraph__get_dependencies(file_path, repo, direction, depth)` — import + call dependency tree

### Workflow
1. Call ToolSearch to load the tools (see above) before the first orgraph call each session.
2. Determine the project root (e.g. `/Users/you/my-project`) — pass it as `repo` to every call.
3. Start with `mcp__orgraph__search` to find relevant code by description.
4. Use `mcp__orgraph__trace` to follow call chains — don't read files to discover callers/callees.
5. Use `mcp__orgraph__get_context` to understand where a file/symbol fits architecturally before editing.
6. Use `mcp__orgraph__find_entry_points` to map the API surface of an unfamiliar codebase.
7. Use `mcp__orgraph__get_dependencies` to understand what a file pulls in before refactoring it.
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
