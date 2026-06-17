# Changelog

## 0.1.6 - 2026-06-17

### Fixed
- Codex config: was writing `[[mcp_servers]]` (array-of-tables, invalid) — now writes `[mcp_servers.orgraph]` (correct inline table format); auto-migrates stale entries on uninstall
- Opencode config: was missing `"type": "local"` and `"enabled": true` — Opencode rejects entries without these

## 0.1.5 - 2026-06-17

### Fixed
- MCP server now starts in <1s instead of 60s+ — was blocking stdio handshake while auto-indexing, causing Claude Code to time out and show "Failed to connect". Indexing now runs in a background thread; tools return "indexing in progress" until ready.

## 0.1.4 - 2026-06-17

### Fixed
- `orgraph install` for Claude Code now writes to `~/.claude/settings.json` (where Claude Code actually reads MCP config) instead of `~/.claude.json` (which it ignores for MCP)
- Removed `"type": "stdio"` from Claude Code MCP entry — Claude Code doesn't use it and it caused confusion

## 0.1.3 - 2026-06-17

### Fixed
- `serve` no longer crashes with `TypeError` when auto-indexing (Console.print doesn't accept `file=`)
- `serve` auto-migrates stale single-file `graph.kuzu` (kuzu 0.8 format) to the new directory format on startup

## 0.1.2 - 2025-06-17

### Fixed
- `orgraph --version` now works correctly (package_name was `orgraph`, should be `orgraph-mcp`)

## 0.1.1 - 2025-06-17

### Fixed
- Cap `requires-python < 3.14` and `kuzu < 0.10` — no pre-built wheels exist for Python 3.14 yet, causing install failure when uv picked the latest Python

## 0.1.0 - 2025-06-17

### Added
- `orgraph index` — extract nodes/edges via tree-sitter (SCIP fallback), build topology clusters, Leiden communities, and hybrid search index
- `orgraph status` — show graph stats, topology clusters, and community sizes
- `orgraph search` — hybrid BM25+semantic code search
- `orgraph serve` — FastMCP stdio server with 6 tools: `search`, `trace`, `get_context`, `find_entry_points`, `get_dependencies`, `reindex`
- `orgraph eval` — retrieval eval harness with NDCG@10, MRR, Precision@k
- `orgraph install` / `orgraph uninstall` — interactive installer for Claude Code, Cursor, Codex, VS Code, Gemini CLI, Opencode
- Auto-index on `serve` — no manual `orgraph index` needed on first run
- Incremental `reindex` MCP tool — detects changed/deleted files via md5 manifest, re-extracts only what changed, swaps state live without server restart
- Fully vendored graphify extractor — standalone install, no external path dependencies
