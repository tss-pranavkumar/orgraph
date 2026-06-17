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
    """Append an [[mcp_servers]] block for orgraph to a TOML config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    block = (
        '\n[[mcp_servers]]\n'
        'name = "orgraph"\n'
        'command = "uvx"\n'
        'args = ["--from", "orgraph-mcp", "orgraph", "serve", "."]\n'
    )
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if "orgraph" in existing:
        return "unchanged"
    path.write_text(existing + block, encoding="utf-8")
    return "created" if not existing.strip() else "updated"


def remove_toml_mcp(path: Path) -> Action:
    """Remove the orgraph [[mcp_servers]] block from a TOML config."""
    if not path.exists():
        return "not-found"
    text = path.read_text(encoding="utf-8")
    if "orgraph" not in text:
        return "not-found"
    lines = text.splitlines(keepends=True)
    out, skip = [], False
    for line in lines:
        if line.strip() == "[[mcp_servers]]":
            # peek ahead: if next non-empty line has orgraph, skip this block
            skip = False  # reset; handled below
        if skip and line.strip().startswith("[["):
            skip = False
        if not skip:
            out.append(line)
        if 'name = "orgraph"' in line:
            # remove the block we just added (last [[mcp_servers]] in out)
            while out and out[-1].strip() != "[[mcp_servers]]":
                out.pop()
            if out:
                out.pop()
            skip = True
    path.write_text("".join(out), encoding="utf-8")
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
