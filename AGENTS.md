# AGENTS.md — orgraph

Authoritative agent guide for the orgraph codebase. Read this before making changes.

## Current version: 0.1.31

## What orgraph does

Points at any repo → builds a persistent Kuzu knowledge graph + topology clusters + Leiden communities → serves it via MCP so coding agents (Cursor, Claude CLI, Codex) can query architectural context.
## Project Snapshot

T3 Code is a minimal web GUI for using coding agents like Codex and Claude.

This repository is a VERY EARLY WIP. Proposing sweeping changes that improve long-term maintainability is encouraged.

## Core Priorities

1. Performance first.
2. Reliability first.
3. Keep behavior predictable under load and during failures (session restarts, reconnects, partial streams).

If a tradeoff is required, choose correctness and robustness over short-term convenience.
## Maintainability


Long term maintainability is a core priority. If you add new functionality, first check if there is shared logic that can be extracted to a separate module. Duplicate logic across multiple files is a code smell and should be avoided. Don't be afraid to change existing code. Don't take shortcuts by just adding local logic to solve a problem.
#Reference Docs 
.codes directory contain reference repo 

Use these as implementation references when designing protocol handling, UX flows, and operational safeguards.


## Phase status

| Phase | Status | Notes |
|---|---|---|
| P1 Foundation + Extraction | ✅ COMPLETE | `orgraph index` + `orgraph status` working, 21 tests pass |
| P2 Topology + Communities | ✅ COMPLETE | BFS clusters + Leiden communities wired into `orgraph index` and `orgraph status` |
| P3 Search | ✅ COMPLETE | `orgraph search` works; semble BM25+Model2Vec; 9 tests |
| P4 MCP Server | ✅ COMPLETE | FastMCP stdio, 5 tools, 12 tests |
| P5 Eval Harness | ✅ COMPLETE | NDCG@10=0.903, MRR=0.895 on codewiki (target >0.70); 17 tests |

## Package layout

```
orgraph/
  cli.py                  # Click CLI: index / status / search / serve
  extract/
    types.py              # NodeDict, EdgeDict, ExtractionResult, make_uid()
    manifest.py           # Manifest — file → md5 tracking (.orgraph/manifest.json)
    scip.py               # ScipExtractor — compiler-accurate, falls back to None
    scip_pb2.py           # Auto-generated protobuf bindings (SCIP format)
    treesitter.py         # TreeSitterExtractor — wraps graphify; _GRAPHIFY_ROOT must exist
  graph/
    kuzu.py               # OrgraphDB — thin Kuzu wrapper; do NOT mkdir db_path itself
    schema.py             # create_schema(db) — all node/edge tables (IF NOT EXISTS)
    builder.py            # GraphBuilder.ingest(result) → (nodes, edges); .clear() wipes all nodes
    pipeline.py           # build_index(db, repo, dir) — shared full-build used by CLI + reindex
  topology/
    call_graph.py         # CallGraph, CallEdge, GraphRelation data structures
    context.py            # RepoContext shim; build_repo_context(result, repo_path)
    topology.py           # build_topology_map(ctx) → TopologyMap (BFS clusters)
    cluster.py            # cluster(G) → Leiden communities; build_nx_graph_from_result()
    serialise.py          # save/load topology.json and communities.json
  search/
    index.py              # SearchIndex stub (P3)
  mcp/
    server.py             # start_server() stub (P4)
  eval/
    __init__.py           # empty (P5)
```

## Marketing

- `launch-video/` — Remotion (React) project for the 44s product launch trailer (1920×1080, dark dev-tool aesthetic). VO clips in `launch-video/public/voiceover/` generated locally via macOS `say` (swappable). Render: `cd launch-video && ./node_modules/.bin/remotion render OrgraphTrailer out/orgraph-trailer.mp4`. Numbers shown (NDCG 0.903/0.818, symbol MRR 1.000, 18k nodes) are from live evals — keep them in sync with `orgraph eval`.

## State written to indexed repos

```
<repo>/.orgraph/
  graph.kuzu/       # Kuzu DB directory (kuzu 0.9+; old single-file format auto-migrated on serve)
  manifest.json     # file → md5
  topology.json     # TopologyMap serialised
  communities.json  # Leiden {community_id: [node_uids]}
```

## Architecture invariants

- **Kuzu DB path**: Kuzu 0.9+ creates a *directory* at `db_path`. Never `mkdir(db_path)` — only `mkdir(db_path.parent)`. `serve` auto-deletes stale single-file `graph.kuzu` (old format) and re-indexes.
- **graphify path**: `extract/treesitter.py` adds `~/tss/codegen/orgraph/.codes/graphify` to `sys.path` at runtime. If `.codes/` moves, update `_GRAPHIFY_ROOT`.
- **graphify label field**: graphify puts display names in `label` (`"authenticate()"`, `"User"`). Not a type tag. Conversion logic lives in `treesitter.py`.
- **tree-sitter grammars**: the bundled extractor (`_vendor/extract.py`) dispatches ~25 languages, each importing its grammar lazily and degrading to 0 nodes if the grammar is absent. Core deps ship Python/JS/TS + Go/Rust/Java/C/C++/C#/Ruby/PHP. Other langs (Kotlin/Scala/Groovy/Lua/Swift) use incompatible release schemes — install their grammar manually to enable. `build_index` warns (CLI) / returns `warnings` (MCP) when code files on disk produce no symbols, so a missing grammar surfaces instead of a silent empty index.
- **`_is_test_file` heuristic**: Files under paths containing `/tests/` or `/test/` are excluded from BFS entry points. Tests that run topology on fixture code must copy fixtures to a non-tests temp dir (`shutil.copytree(FIXTURE, tmp_path / "simple_python")`).
- **Topology depends on ExtractionResult**: `build_repo_context()` builds CallGraph directly from ExtractionResult CALLS edges, not from Kuzu. Topology runs before DB is closed.
- **GraphBuilder swallows write errors → schema/param mismatches are silent data loss**: `_write_nodes`/`_write_edges`/`_write_file_nodes` wrap every `db.execute` in `except Exception: pass`, so a Cypher/schema mismatch drops the node or edge while `ingest()` still reports success. Two failure modes this caused (all fixed + regression-tested in `tests/test_graph.py`): (1) Kuzu **rejects unused query params** — `_node_params` returns http_method/http_path, but the Class/Interface/Struct/Enum/Variable MERGE templates don't reference them, so every non-Function node was dropped; fixed by `_used_params(cypher, params)` filtering to placeholders actually in the query. (2) `_write_edges` unconditionally `SET r.line_number`/`r.call_kind`, so any rel table missing those columns drops every edge — INHERITS shipped without `line_number`, and pre-`call_kind` DBs lack `call_kind` on CALLS. When you add a column to an existing rel/node table in `schema.py`, you MUST also add an idempotent `ALTER TABLE ... ADD ...` to `_MIGRATIONS` — `CREATE ... IF NOT EXISTS` never alters an existing table, so old `.orgraph/graph.kuzu` DBs keep the stale shape and silently reject writes. Verify changes by querying stored counts (`MATCH (c:Class) RETURN count(c)`), not by trusting `ingest()`'s return. **As of the data-loss fix:** `_write_edges` no longer SETs a fixed column list — it builds the SET clause from `_EDGE_COLUMNS[relation]` (mirror of the schema rel columns; keep in lockstep) so it never SETs a column a table lacks (this was the IMPORTS `confidence` crash that dropped 100% of imports). It also writes via `MERGE ... RETURN count(r)` and records per-relation `extracted`/`persisted`/`drops`-by-reason on `builder.last_ingest_summary`; `ingest()` logs a WARNING on any whole-relation drop or `schema-error`. Drop reasons: `schema-error`/`no-label-pair` = bugs; `unresolved-external`/`dst-missing-external`/`self-import` = legitimate. `_EDGE_TABLES` must only list valid schema FROM/TO pairs of uid-keyed symbol tables (File/Directory CONTAINS is written separately in `_write_file_nodes`; File has no `uid`, so a `MATCH (:File {uid})` raises).
- **IMPORTS is File→File (path-keyed), not File→Module**: the `Module` node table is never populated. graphify emits imports as a mix of (file → module-name) and (module-name → symbol) where module names are bare strings, not node ids. `treesitter._resolve_import_edges` resolves both endpoints to a file path (via `id_to_path` for files/symbols, or `module_to_path` keyed by module stem) and emits deduped File→File edges carrying `src_path`/`dst_path`. `builder._write_import` matches both `File` nodes by `path`. `query.get_dependencies` reads `(File)-[:IMPORTS]->(File)` (imports) and the reverse (imported_by). **Both extractors emit IMPORTS**: tree-sitter via `_resolve_import_edges`; SCIP via Pass 4 in `_parse_scip` (scip-python/scip-typescript do NOT set the SCIP Import role — verified — so imports are detected by `_IMPORT_LINE_RE` matching the source line a reference occurrence sits on, then resolving the symbol to its defining file via `sym_def_path`). So `deps` works for SCIP-extracted TypeScript repos too, not just tree-sitter. **Kuzu rel FROM/TO is immutable** — `schema._drop_legacy_imports` detects a legacy `File→Module` IMPORTS table (via `show_connection`, matching `Module`) and DROPs it so the create loop recreates File→File; safe because the old table never persisted any edge. A FROM/TO change to a table that holds data (e.g. adding `Class→Class` to CALLS) needs a full reindex, not an ALTER.
- **Python type-resolution pass (`extract/pyresolve.py`)**: after `_convert`, `TreeSitterExtractor.run()` calls `resolve_python_calls(result, files)`, which re-parses each `.py` with `tree-sitter-python` and rewrites receiver-typed calls graphify only name-matches: `var = Class(); var.m()` → `Class.m` (constructor-inferred local binding), `self.attr = Class(); self.attr.m()` → `Class.m`, and `super().m()` → `Base.m` (first base). Resolved edges get `call_kind="resolved"`; the pass supersedes the stale name-matched CALLS edge for the same `(caller_uid, bare_method)` and dedups. 80/20 scope — no fixpoint/flow-sensitivity/cross-file return types/full MRO (those stay name-matched). A variable reassigned to a *different* class in the same function is treated as ambiguous and left unresolved (prefer a missing edge over a misleading one — never emit a confidently-wrong receiver type). A bogus binding is also harmless because a resolved edge is only emitted when its `ClassName.method` target matches a real node uid. Clean-room (no GitNexus code — their type-resolver is PolyForm-Noncommercial).
- **Call-graph correctness harness**: `orgraph/eval/callgraph_fixtures.py` holds ~24 ground-truth fixtures (`true_edges`/`forbidden_edges`, `xfail` for deferred patterns), driven by the parametrized gate `tests/test_callgraph_patterns.py` and the manual scoreboard `callgraph_truth_eval.py` (run `uv run python callgraph_truth_eval.py`). This is the measurable contract for call accuracy — flip a fixture's `xfail=False` when the resolver lands its pattern.
- **SCIP extraction (`extract/scip.py`)**: `extract_repo` prefers SCIP when a `scip-<lang>` binary is on PATH, else tree-sitter. scip-python is an **npm** package (`@sourcegraph/scip-python`), invoked as `scip-python index --cwd <repo> --output <f> --quiet`. It leaves `SymbolInformation.kind=0` / `display_name` empty — so `_parse_scip` decodes the SCIP **symbol descriptor string** (`_name_from_symbol`, `_label_from_symbol`) and reconstructs CALLS from reference occurrences using `Occurrence.enclosing_range` (`_find_enclosing_symbol`) + a read-from-disk `(`-after-token check (docs carry no embedded `.text`). **Class vs interface vs enum**: the descriptor `#` suffix is identical for every type, so `_label_from_symbol` returns a coarse `Type`; `_refine_type_label` then reads the declaration keyword from `SymbolInformation.documentation` (e.g. ` ```ts\ninterface Foo\n``` `, populated by both scip-python and scip-typescript) to emit `Class`/`Interface`/`Enum`/`Struct`, skipping bare `type` aliases. Without this every TS interface was mislabeled `Class` (a real repo showed 160 "Class" that were really 21 class + 125 interface + 14 type-alias). Falcon routes + celery dispatch are reused from the tree-sitter extractor for parity. Measured on the TSS backend: SCIP CALLS are ~93% compiler-EXTRACTED vs tree-sitter's ~42%, dropping name-collision false positives and catching super()/cross-class calls tree-sitter misses. The descriptor decoder + enclosing-range call graph are **language-agnostic** (they read the standard SCIP descriptor grammar, not Python specifics) — verified against `scip-typescript` on a real TS repo (nodes, cross-file CALLS, and `extends`→INHERITS all resolve; `scip-typescript` does populate `enclosing_range`). The two Python-only pieces are the install hint and the `--cwd` build-command branch; the Falcon/Celery heuristics are Python-framework-specific (harmless no-ops elsewhere). Test fixtures: `tests/fixtures/simple_python.scip` and `tests/fixtures/simple_typescript.scip` (both committed; CI needs no binary). Live end-to-end tests are `skipif`-guarded on the binary being on PATH. **scip-python is environment-fragile**: it only emits documents when Pyright can resolve the repo's venv/imports — on an unresolved environment it writes a valid header with **zero documents** (silent failure). `ScipExtractor.run()` therefore falls back to tree-sitter (with a stderr warning) whenever SCIP yields 0 nodes, so you always get a populated graph; tree-sitter is the floor most Python users actually hit (scip-typescript is reliable; scip-python is a bonus when the env cooperates).
- **Leiden falls back to Louvain**: If `graspologic` is not installed, networkx Louvain is used. Both give stable results via seed=42.

## CLI commands

```bash
orgraph index <repo>              # extract → kuzu → topology → leiden → save
orgraph status <repo>             # node/edge counts + cluster table + community count
orgraph search <q> <r>            # hybrid BM25+semantic search
orgraph trace <sym> <repo>        # call chain: what a symbol calls (default) or --callers
orgraph who-calls <sym> <repo>    # all callsites for a symbol, with file:line
orgraph path <from> <to> <repo>   # shortest call path between two symbols (BFS via CALLS edges)
orgraph file <file> <repo>        # list all functions/classes defined in a file
orgraph context <file|sym> <repo>  # architectural context: cluster, community, indegree, peers
orgraph entry-points <repo>        # HTTP handlers and async tasks (--kind http|tasks|all)
orgraph deps <file> <repo>         # import/dependency tree (--direction imports|imported_by)
orgraph serve <repo>              # MCP server (stdio)
```

## Testing

```bash
cd ~/tss/codegen/orgraph
uv run python -m pytest tests/ -q    # all 21 tests
uv run python -m pytest tests/test_extract.py tests/test_graph.py -q   # P1 only
uv run python -m pytest tests/test_topology.py -q                       # P2 only
```

## Reference repos (all at `~/tss/codegen/orgraph/.codes/`)

| Repo | What we borrow |
|---|---|
| `CodeGraphContext` | SCIP extractor, Kuzu adapter, graph schema, scip_pb2.py |
| `graphify` | tree-sitter extractor, Leiden cluster.py |
| `semble` | pip dep only — hybrid BM25+Model2Vec search |
| `codewiki` (`~/tss/codegen/codewiki`) | CallGraph data structures, BFS topology clustering |

## Next phase: P5 — Eval Harness

Build `orgraph/eval/` with:
- `ground_truth.py` — `EvalQuery` dataclass (query, relevant_files, relevant_symbols, query_type)
- `metrics.py` — `ndcg_at_k()`, `mrr()`, `precision_at_k()`
- `runner.py` — `EvalRunner(repo_path, ground_truth_path).run() → EvalReport`
- `fixtures/codewiki_gt.json` — 20+ ground-truth Q&A pairs for codewiki
- CLI: `orgraph eval <repo> --ground-truth <path>`
- Target: NDCG@10 > 0.70 on codewiki

## MCP tool notes (P4)

- `register_tools(mcp, db, idx, topology, communities, repo_path)` returns `dict[str, Callable]` — use this in tests, don't introspect FastMCP internals
- FastMCP 3.4.2: `list_tools()` is async, `get_tool(name)` is async — bypass both by using the returned dict
- Tool functions are defined as closures over `db`, `idx`, `topology`, `communities`, `repo_path`

## reindex semantics (v0.1.24)

- **`reindex` is a manifest-gated full rebuild, not a delta patch.** If the file-hash manifest
  reports no changes → no-op (`status: up_to_date`). Otherwise it calls `graph/pipeline.build_index`,
  which wipes the graph (`GraphBuilder.clear()`) and rebuilds graph + topology + communities from a
  single cache-backed extraction, then re-embeds search. `orgraph index` shares the same
  `build_index` so CLI and MCP can never diverge.
- **Why full, not delta:** the old delta path `delete_file_nodes(changed)` did `DETACH DELETE`,
  which dropped incoming `caller→callee` CALLS edges from *unchanged* callers; re-extracting only the
  changed file never re-created them, so the graph eroded on every reindex. Regression test:
  `tests/test_mcp_tools.py::test_reindex_rebuilds_and_preserves_cross_file_edges`.
- **Extraction is AST-cached** (graphify cache keyed by file hash), so the full re-extract is cheap
  for unchanged files. Search is always a full re-embed — **semble has no incremental API**.
- **Tool honesty:** `trace` / `get_dependencies` / `get_context` return a `truncated` flag when a
  result cap is hit; `find_entry_points` appends a `truncation_notice` item (kept list-shaped, with a
  `symbol` key, so existing callers don't break). `trace` returns `alternatives` + a `note` when a
  name is ambiguous and accepts `file=` (a path fragment) to pin a specific definition.

## Known follow-up

- The graphify extractor writes its AST cache to `graphify-out/` in the **indexed repo root**
  (cache lands at `<root>/<GRAPHIFY_OUT>/cache`). It should live under `.orgraph/` instead — set the
  `GRAPHIFY_OUT` env var (accepts an absolute path) before extraction. Deferred because a fixture
  cache is committed under `tests/fixtures/simple_python/graphify-out/` and relocating orphans
  existing caches across already-indexed repos.
