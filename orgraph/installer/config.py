"""Read/write helpers for agent MCP config files (JSON and TOML)."""
from __future__ import annotations

import json
from pathlib import Path

from orgraph.installer.agents import Action, ORGRAPH_END, ORGRAPH_START


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


def merge_json_mcp(path: Path, key: str, entry: dict) -> Action:
    """Add orgraph to the MCP servers block in a JSON config file."""
    data = _read_json(path)
    servers: dict = data.setdefault(key, {})
    existing = servers.get("orgraph")
    if existing == entry:
        return "unchanged"
    servers["orgraph"] = entry
    _write_json(path, data)
    return "updated" if existing else "created"


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

def merge_toml_mcp(path: Path) -> Action:
    """Add [mcp_servers.orgraph] block to a Codex config.toml."""
    path.parent.mkdir(parents=True, exist_ok=True)
    block = (
        '\n[mcp_servers.orgraph]\n'
        'command = "uvx"\n'
        'args = ["--from", "orgraph-mcp", "orgraph", "serve", "."]\n'
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
