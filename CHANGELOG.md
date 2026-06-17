# Changelog

## 0.1.9 - 2026-06-17

### Fixed
- `orgraph install` now writes Claude Code MCP config to the project-scoped `projects[abs_path][mcpServers]` key in `~/.claude.json` instead of the global `mcpServers` key. The global key is loaded at daemon start with no project context, so `serve .` resolved to the wrong directory — the server either failed to find an index or served the wrong repo. The new entry matches exactly what `claude mcp add` produces and is only activated when Claude Code opens that project.
- `'.'` placeholder in MCP args is now replaced with the absolute repo path for all agents (not just Claude Code), so agents that launch the server from a different working directory always get the correct index.
- Stale global-level `orgraph` entries written by earlier installs are automatically cleaned up on the next `orgraph install` run.
- `orgraph install` and `orgraph uninstall` now accept an optional `REPO_PATH` argument (default: current directory) so you can register a repo you're not currently `cd`'d into.

### Added
- `trace` and `get_context` MCP tools now resolve Class nodes in addition to Function nodes, so class-based symbols (e.g. Falcon resource classes) no longer return `found: false`.
- `get_context` now reports symbol-level indegree (incoming CALLS edges in the graph) instead of file-level indegree from the topology map — previously returned 0 for symbols called only within their own file.
- Falcon HTTP handlers (`on_get`, `on_post`, `on_put`, `on_patch`, `on_delete`, `on_options`, `on_head`) are now detected during indexing and populate the `http_method` field, so `find_entry_points(kind="http")` surfaces them correctly.
- Python class methods are now stored as `ClassName.method_name` in both the SCIP and TreeSitter extractors, preventing uid collisions when multiple resource classes define the same method name.

## 0.1.8 - 2026-06-17

### Fixed
- `orgraph install` crash: `_VSCODE_MCP_ENTRY` deleted but VS Code still referenced it — now uses `_MCP_ENTRY`

## 0.1.7 - 2026-06-17

### Fixed
- Claude Code: was writing to `~/.claude/settings.json` (which Claude Code ignores for MCP) — now correctly writes to `~/.claude.json` per docs and semble reference
- Opencode: command must be an array `["uvx", ...]` not `"uvx"` string + separate `args` — Opencode's schema rejects the split format
- Add `_opencode_mcp_path()` that checks `.jsonc` before `.json` (matches opencode's config search order)

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
