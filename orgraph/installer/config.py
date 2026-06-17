"""Read/write helpers for agent MCP config files (JSON and TOML)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from orgraph.installer.agents import Action, ORGRAPH_END, ORGRAPH_START, _resolve_orgraph_bin


# ── Claude Code CLI helpers ───────────────────────────────────────────────────

def _claude_bin() -> str | None:
    return shutil.which("claude")


def claude_mcp_add(name: str, command: str, args: list[str], scope: str = "user") -> Action:
    """Register an MCP server via `claude mcp add` so Claude Code owns the config."""
    claude = _claude_bin()
    if not claude:
        return "error"
    # `claude mcp add` errors if the entry already exists — remove first if present
    subprocess.run([claude, "mcp", "remove", "-s", scope, name], capture_output=True)
    result = subprocess.run(
        [claude, "mcp", "add", "-s", scope, name, command, *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return "error"
    return "created"


def claude_mcp_remove(name: str, scope: str = "user") -> Action:
    """Remove an MCP server via `claude mcp remove`."""
    claude = _claude_bin()
    if not claude:
        return "error"
    result = subprocess.run(
        [claude, "mcp", "remove", "-s", scope, name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return "not-found"
    return "removed"


def _remove_stale_project_scoped(path: Path) -> None:
    """Remove any project-scoped orgraph entries left by older installs.

    Uses direct JSON edit because `claude mcp remove -s local` would need
    to be run from each project's directory individually.
    """
    data = _read_json(path)
    changed = False
    for proj_val in data.get("projects", {}).values():
        mcp = proj_val.get("mcpServers", {})
        for key in ("orgraph", "orgraph-sync"):
            if key in mcp:
                del mcp[key]
                changed = True
    if changed:
        _write_json(path, data)


# ── JSON helpers ─────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bake_repo_path(entry: dict, repo_path: Path) -> dict:
    """Replace '.' placeholder in args with the absolute repo path."""
    args = entry.get("args", [])
    return {**entry, "args": [str(repo_path) if a == "." else a for a in args]}


def merge_json_mcp(path: Path, key: str, entry: dict, remove_project_scoped: bool = False) -> Action:
    """Add orgraph to the MCP servers block in a JSON config file.

    remove_project_scoped: if True, also removes any project-scoped orgraph entries
    from ~/.claude.json projects[*][mcpServers] to avoid scope conflicts.
    """
    data = _read_json(path)
    if remove_project_scoped:
        for proj_val in data.get("projects", {}).values():
            proj_val.get("mcpServers", {}).pop("orgraph", None)
            proj_val.get("mcpServers", {}).pop("orgraph-sync", None)
    servers: dict = data.setdefault(key, {})
    existing = servers.get("orgraph")
    if existing == entry and not remove_project_scoped:
        return "unchanged"
    servers["orgraph"] = entry
    _write_json(path, data)
    return "updated" if existing else "created"


def merge_claude_mcp(path: Path, repo_path: Path, entry: dict) -> Action:
    """Write orgraph to Claude Code's project-scoped mcpServers in ~/.claude.json.

    Claude Code keys MCP servers per-project under projects[abs_path][mcpServers].
    Writing there (instead of the global mcpServers) means the server is only
    started for that repo and gets the correct absolute path at startup.
    Also cleans up any stale global-level entry written by older installs.
    """
    data = _read_json(path)
    baked = _bake_repo_path(entry, repo_path)

    # Remove stale global-level entry if present
    data.get("mcpServers", {}).pop("orgraph", None)

    project = data.setdefault("projects", {}).setdefault(str(repo_path), {})
    servers: dict = project.setdefault("mcpServers", {})
    existing = servers.get("orgraph")
    if existing == baked:
        return "unchanged"
    servers["orgraph"] = baked
    _write_json(path, data)
    return "updated" if existing else "created"


def remove_claude_mcp(path: Path, repo_path: Path) -> Action:
    """Remove orgraph from Claude Code's project-scoped mcpServers."""
    if not path.exists():
        return "not-found"
    data = _read_json(path)
    removed = False
    # Remove project-scoped entry
    project = data.get("projects", {}).get(str(repo_path), {})
    if "orgraph" in project.get("mcpServers", {}):
        del project["mcpServers"]["orgraph"]
        removed = True
    # Also clean up any global-level entry
    if "orgraph" in data.get("mcpServers", {}):
        del data["mcpServers"]["orgraph"]
        removed = True
    if not removed:
        return "not-found"
    _write_json(path, data)
    return "removed"


def remove_json_mcp(path: Path, key: str) -> Action:
    """Remove orgraph from the MCP servers block in a JSON config file."""
    if not path.exists():
        return "not-found"
    data = _read_json(path)
    servers: dict = data.get(key, {})
    if "orgraph" not in servers:
        return "not-found"
    del servers["orgraph"]
    _write_json(path, data)
    return "removed"


# ── TOML helper (Codex uses config.toml) ─────────────────────────────────────

def merge_toml_mcp(path: Path, repo_path: Path | None = None) -> Action:
    """Add [mcp_servers.orgraph] block to a Codex config.toml."""
    path.parent.mkdir(parents=True, exist_ok=True)
    orgraph_bin = _resolve_orgraph_bin()
    block = (
        '\n[mcp_servers.orgraph]\n'
        f'command = "{orgraph_bin}"\n'
        'args = ["serve"]\n'
    )
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if "[mcp_servers.orgraph]" in existing:
        return "unchanged"
    # Remove any stale [[mcp_servers]] array-of-tables entry we may have written before
    existing = _remove_array_mcp_block(existing)
    path.write_text(existing + block, encoding="utf-8")
    return "created" if not existing.strip() else "updated"


def _remove_array_mcp_block(text: str) -> str:
    """Remove a stale [[mcp_servers]] array-of-tables block for orgraph if present."""
    import re
    return re.sub(
        r'\n\[\[mcp_servers\]\]\nname = "orgraph"\n(?:[^\[].*)?\n?',
        '',
        text,
        flags=re.MULTILINE,
    )


def remove_toml_mcp(path: Path) -> Action:
    """Remove the [mcp_servers.orgraph] block from a Codex config.toml."""
    if not path.exists():
        return "not-found"
    import re
    text = path.read_text(encoding="utf-8")
    if "orgraph" not in text:
        return "not-found"
    # Remove [mcp_servers.orgraph] table and its keys (stop at next [section])
    cleaned = re.sub(
        r'\n\[mcp_servers\.orgraph\]\n(?:[^\[].*)?\n?',
        '',
        text,
        flags=re.MULTILINE,
    )
    # Also clean up old [[mcp_servers]] array-of-tables format if present
    cleaned = _remove_array_mcp_block(cleaned)
    path.write_text(cleaned, encoding="utf-8")
    return "removed"


# ── Markdown instructions helpers ─────────────────────────────────────────────

def upsert_instructions(path: Path, block: str) -> Action:
    """Insert or replace the orgraph instructions block in a Markdown file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if ORGRAPH_START in existing:
        start = existing.index(ORGRAPH_START)
        end = existing.index(ORGRAPH_END) + len(ORGRAPH_END)
        new_text = existing[:start] + block.strip() + existing[end:]
        path.write_text(new_text, encoding="utf-8")
        return "updated"
    path.write_text((existing.rstrip() + "\n\n" + block.strip() + "\n").lstrip("\n"), encoding="utf-8")
    return "created" if not existing.strip() else "updated"


def remove_instructions(path: Path) -> Action:
    """Remove the orgraph instructions block from a Markdown file."""
    if not path.exists():
        return "not-found"
    text = path.read_text(encoding="utf-8")
    if ORGRAPH_START not in text:
        return "not-found"
    start = text.index(ORGRAPH_START)
    end = text.index(ORGRAPH_END) + len(ORGRAPH_END)
    path.write_text(text[:start].rstrip() + "\n" + text[end:].lstrip("\n"), encoding="utf-8")
    return "removed"
