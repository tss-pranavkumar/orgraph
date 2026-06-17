# AGENTS.md — orgraph

Authoritative agent guide for the orgraph codebase. Read this before making changes.

## Current version: 0.1.23

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
    builder.py            # GraphBuilder.ingest(result) → (nodes, edges) written
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
- **tree-sitter grammars**: `tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript` must be installed (in `pyproject.toml` deps).
- **`_is_test_file` heuristic**: Files under paths containing `/tests/` or `/test/` are excluded from BFS entry points. Tests that run topology on fixture code must copy fixtures to a non-tests temp dir (`shutil.copytree(FIXTURE, tmp_path / "simple_python")`).
- **Topology depends on ExtractionResult**: `build_repo_context()` builds CallGraph directly from ExtractionResult CALLS edges, not from Kuzu. Topology runs before DB is closed.
- **Leiden falls back to Louvain**: If `graspologic` is not installed, networkx Louvain is used. Both give stable results via seed=42.

## CLI commands

```bash
orgraph index <repo>     # extract → kuzu → topology → leiden → save
orgraph status <repo>    # node/edge counts + cluster table + community count
orgraph search <q> <r>   # search (P3 stub)
orgraph serve <repo>     # MCP server (P4 stub)
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
