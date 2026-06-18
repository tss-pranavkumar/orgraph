# Changelog

## 0.1.28 - 2026-06-18

### Added
- **`orgraph deps <file>`** — new CLI command that shows the import/dependency tree for a file.
  Accepts `--direction imports|imported_by` (default: `imports`) and `--depth` (default: 1, max 3).
  Equivalent to the `get_dependencies` MCP tool. CLI and MCP are now fully in sync.

## 0.1.27 - 2026-06-18

### Added
- **`orgraph context <file|symbol>`** — new CLI command that shows architectural context for a file
  path or symbol name: topology cluster, cluster file count, foundational flag, Leiden community ID,
  call depth, indegree, related cluster files, and community peers (functions that tend to change
  together). Equivalent to the `get_context` MCP tool, now available without an AI client.
- **`orgraph entry-points`** — new CLI command that lists HTTP handlers and async (Celery) tasks
  detected in the indexed repo as a Rich table. Accepts `--kind http|tasks|all` (default: `http`).
  Equivalent to the `find_entry_points` MCP tool.

## 0.1.26 - 2026-06-17

### Fixed
- **SCIP extraction actually works now.** It had never produced a usable index — three bugs, all fixed:
  the install hint was wrong (scip-python is an **npm** package, not pip); `_build_command` passed a
  stale `--project-root` flag (scip-python wants `--cwd`); and `_parse_scip` classified symbols off
  `SymbolInformation.kind`/`display_name`, which scip-python leaves empty, so it yielded 0 nodes.
- **Rewrote `_parse_scip`** (techniques adapted from CodeGraphContext): decode the SCIP **symbol
  descriptor string** for name + kind (`_name_from_symbol`, `_label_from_symbol`), and reconstruct the
  call graph from reference occurrences via `Occurrence.enclosing_range` (`_find_enclosing_symbol`) plus
  a read-from-disk "next token is `(`" check (scip-python documents carry no embedded text). Methods are
  class-qualified (`Class.method`) to match tree-sitter naming; Falcon routes + celery dispatch are
  reused from the tree-sitter extractor so `find_entry_points` works identically under SCIP.

### Measured (TSS backend, scip-python vs tree-sitter)
- SCIP CALLS edges are ~93% compiler-EXTRACTED vs tree-sitter's ~42% (rest are heuristic guesses).
- Repo-wide call-pair diff: 2,389 agree; 3,558 tree-sitter-only (mostly class-name-collision false
  positives like `→ User`/`→ UserEAV`); 285 SCIP-only real calls tree-sitter missed (super()/`__init__`
  chains, cross-class method dispatch, resolved cross-module functions).

### Tests
- `tests/test_scip.py` against a committed `tests/fixtures/simple_python.scip` (no binary needed in CI);
  live end-to-end test guarded by `skipif(scip-python not installed)`.

### Added
- **CLI nudge:** when `orgraph index` falls back to tree-sitter but a SCIP indexer exists for the repo's
  primary language (and isn't installed), it prints a one-line tip to install `scip-<lang>` for a
  higher-precision call graph. orgraph then uses it automatically on the next index.

### Note
- SCIP stays **opt-in** (used only when a `scip-<lang>` binary is on PATH). Default remains tree-sitter.

## 0.1.25 - 2026-06-17

### Fixed
- **Go/Java/C/C++/C#/Ruby/PHP/Rust repos no longer index to 0 nodes.** The bundled tree-sitter
  extractor already supports ~25 languages, but the package only shipped the Python/JS/TS grammars —
  so any repo in another language extracted nothing and reported a misleading green "Done. 0 nodes."
  These 8 grammars are now core dependencies. (Verified: a Go repo went 0 → 187 nodes after the fix.)
  Other supported langs (Kotlin, Scala, Groovy, Lua, Swift, …) use incompatible release schemes;
  enable one by installing its grammar into the same env, e.g. `uv pip install tree-sitter-kotlin`.

### Added
- **Loud warning on unextractable files.** `index` and `reindex` now compare code files on disk
  against the extensions that actually produced symbols and warn (CLI) / return `warnings` (MCP) when
  files yielded nothing — naming the extensions and how to enable them — instead of silently
  reporting an empty index.

## 0.1.24 - 2026-06-17

### Fixed
- **`reindex` no longer corrupts the graph or desyncs topology.** The old incremental path
  re-extracted the whole repo *twice*, built topology from only the changed files (so topology and
  communities disagreed), and — via `delete_file_nodes` + `DETACH DELETE` — silently dropped incoming
  `caller→callee` CALLS edges from unchanged callers, eroding the graph on every reindex. `reindex` is
  now a manifest-gated **full rebuild**: no-op when nothing changed, otherwise one cache-backed
  extraction → graph wipe + rebuild → topology + communities from the same result → search re-embed.

### Changed
- **Shared `graph/pipeline.py::build_index`** now powers both `orgraph index` (CLI) and the `reindex`
  MCP tool, so they can't diverge. Added `GraphBuilder.clear()` (wipes all nodes/edges for a clean
  rebuild). `TreeSitterExtractor` passes `cache_root=repo_path` for a stable AST cache.
- **Honest tool results.** `trace`, `get_dependencies`, and `get_context` now return a `truncated`
  flag when a result cap is hit. `find_entry_points` appends a `truncation_notice` item when capped
  (still list-shaped, carries a `symbol` key — backward compatible).
- **`trace` disambiguates ambiguous names.** When several symbols share a name it traces the first,
  lists the rest under `alternatives` with a `note`, and accepts a new `file=` path-fragment argument
  to pin a specific definition (previously it silently used `roots[0]`).

### Known follow-up
- The graphify AST cache still writes to `graphify-out/` in the indexed repo root; it should move
  under `.orgraph/` via the `GRAPHIFY_OUT` env var. Deferred (committed fixture cache + orphaning).

## 0.1.23 - 2026-06-17

### Changed
- **Search results now include enclosing symbol** — both the MCP `search` tool and `orgraph search` CLI now resolve which function or class owns each result's line range and attach `symbol` + `symbol_kind`. Agents no longer need a follow-up `list_symbols` call to know what they're looking at.
- **CLI `orgraph search` redesigned** — relative file paths, function/class name displayed prominently, snippet rendered as proper multiline code (6 lines), raw float score replaced with `●●●` / `●●○` / `●○○` relevance tier based on ratio to top result.

## 0.1.22 - 2026-06-17

### Changed
- **Extracted `graph/query.py`** — all raw Kuzu queries moved to a single shared module. `cli.py` and `mcp/tools.py` now call `query.*` functions instead of embedding query strings. No duplicate BFS or symbol-lookup logic anywhere.
- **Moved `open_db_readonly()` to `graph/kuzu.py`** — the context manager that copies the DB to a temp dir (avoiding lock conflicts with the running MCP server) now lives in the DB layer where it belongs. `cli.py` imports it from there.
- `_symbols_for_file` and `_resolve_indexed_file_path` in `mcp/tools.py` are now thin shims over `query.get_file_symbols` and `query.resolve_file_path`.

## 0.1.21 - 2026-06-17

### Added
- **`orgraph who-calls <symbol> <repo>`** — shows every caller of a function with file + line, works even while MCP server is running
- **`orgraph trace <symbol> <repo>`** — prints the full call chain (what a function calls, indented by depth); `--callers` flag reverses direction; `--depth N` controls hops
- **`orgraph file <path> <repo>`** — lists all functions and classes defined in a file, ordered by line number
- **Read-only CLI DB access** — all three commands copy the Kuzu DB to a temp directory so they can run concurrently with the MCP server without lock conflicts
- Graceful `call_kind` fallback — trace works on indexes built before v0.1.20 (missing column)
- Updated `AGENTS.md` version to 0.1.21

## 0.1.20 - 2026-06-17

### Added
- **`list_symbols` MCP tool** — given a file path, returns all functions/classes defined in it ordered by source line. Resolves by absolute path, filename, or path fragment.
- **Celery dispatch detection** — `_extract_celery_dispatch_edges()` in `treesitter.py` scans Python files for `.apply_async(` and `.delay(` calls, resolves the enclosing function as caller, and emits `CALLS` edges tagged `call_kind=celery_dispatch`. Async task boundaries are now visible in the call graph.
- **Falcon HTTP route extraction** — `_collect_falcon_routes()` parses `app.add_route("/path", ClassName())` across all Python files and populates `http_path` on handler nodes. `find_entry_points(kind="http")` now returns routes alongside methods.
- **`find_entry_points(kind="tasks")`** — was documented but unimplemented (dead branch). Now queries CALLS edges with `call_kind=celery_dispatch` and returns caller + task name + file + line.
- **Module/file-level trace fallback** — when `trace("SomeModule")` finds no exact function/class match, returns a `candidates` list of symbols defined in the matching file instead of a silent `found: false`.
- **Community peers in `get_context`** — response now includes `community_peers`: up to 10 co-located symbols from other files in the same Leiden community.
- **`call_kind` column on CALLS edge table** — schema updated; stale indexes without this column are auto-detected and re-indexed on server start.

### Changed
- Search snippet length increased from 400 to 1000 characters.

## 0.1.19 - 2026-06-17

### Changed
- **Conformed with semble installer pattern**: `orgraph install` now supports a third integration — **Sub-agent** — which writes a dedicated `orgraph-explore` sub-agent file to each agent's global agents directory (e.g. `~/.claude/agents/orgraph-explore.md`). Sub-agent files are loaded from package resources (`orgraph/agents/<id>.md`) via `importlib.resources`.
- Added 8 new agent targets: Kiro, Windsurf, Zed, Reasonix, Pi, Command Code, GitHub Copilot, Antigravity — matching the full semble agent list.
- `McpConfig` now has a `format: Literal["json", "toml"]` field (default `"json"`); TOML detection uses `format == "toml"` instead of checking the key name.
- `WriteResult` moved to `agents.py` (matches semble pattern).
- `AgentTarget` now has `subagent_path: Path | None` and a `resolved_mcp_path()` method.
- Integration label width is now dynamic (`max(len(i.label))`), not hardcoded.
- `_ACTION_DETAIL` dict added for human-readable error/skipped messages in installer output.

## 0.1.18 - 2026-06-17

### Changed
- `orgraph install` now writes agent-specific instruction blocks: `CLAUDE_MD_BLOCK` for Claude Code (includes ToolSearch step), `AGENTS_MD_BLOCK` for Codex/Gemini/Opencode (no ToolSearch — tools load automatically). Previously all agents got the Claude-specific ToolSearch instruction which was irrelevant to them.

## 0.1.17 - 2026-06-17

### Changed
- CLAUDE.md block rewritten to be directive: explicitly tells Claude to use orgraph tools **instead of** grep/find/Read, with a substitution table and per-tool guidance on when to reach for each one.

## 0.1.16 - 2026-06-17

### Fixed
- `orgraph install` for Claude Code: `claude mcp add` errors if the entry already exists — now removes the existing entry first, then re-adds. Previously showed `mcp (error)` on every reinstall.

## 0.1.15 - 2026-06-17

### Changed
- `orgraph install` for Claude Code now uses `claude mcp add -s user` subprocess instead of writing `~/.claude.json` directly. This lets Claude Code own the config format and is future-proof against format changes. Also cleans up stale project-scoped entries.

## 0.1.14 - 2026-06-17

### Fixed
- `orgraph serve` with no arguments now starts in true global mode (passes `None` to `start_server`) instead of resolving `"."` to cwd and potentially serving the wrong directory. Tools return a clear error if `repo` is not passed per call.

## 0.1.13 - 2026-06-17

### Fixed
- `orgraph install` for Claude Code now removes all project-scoped `orgraph` entries from `~/.claude.json` when writing the global entry. Previously, stale project-scoped entries caused a scope conflict that made Claude Code use the wrong server (or fail entirely).

## 0.1.12 - 2026-06-17

### Changed
- **Global MCP mode** — orgraph now works like semble: one global MCP server shared across all projects, no per-project install. All tools accept a `repo` parameter (absolute path to the project). Install once with `orgraph install` and use it everywhere.
- `orgraph serve` now accepts an optional repo path (still works project-specific if provided). With no path, server starts globally and loads repos lazily per tool call with a per-repo state cache.
- `orgraph install` writes to global `mcpServers` in `~/.claude.json` (or equivalent) — no more project-scoped entries, no more per-repo `orgraph install` runs.
- CLAUDE.md block updated to instruct passing `repo` with each tool call.

## 0.1.11 - 2026-06-17

### Fixed
- `orgraph install` now writes the MCP entry using the **currently-running orgraph binary** (`sys.argv[0]`) instead of `uvx --from orgraph-mcp`. Previously, every install wrote a command that re-fetched from PyPI at server startup — if the PyPI version was outdated or unpublished, the server silently failed. Now the entry points to whatever binary the user actually ran (`orgraph install` with), so installs work regardless of whether the package is published.

## 0.1.10 - 2026-06-17

### Fixed
- `orgraph install` for Claude Code now writes the orgraph instructions block to `{repo_path}/.claude/CLAUDE.md` (project-level) instead of `~/.claude/CLAUDE.md` (global). Claude Code loads project-level CLAUDE.md at session start with high priority — the global file is often deprioritized, causing Claude to miss the ToolSearch instruction entirely.

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
