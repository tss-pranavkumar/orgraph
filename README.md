# orgraph-mcp

Codebase knowledge graph for coding agents. Gives Claude Code, Cursor, Codex, and other MCP-compatible agents a persistent graph of any repo — call chains, topology clusters, dependency trees — on top of hybrid code search.

## Quickstart

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv tool install orgraph-mcp
orgraph install
```

`orgraph install` detects installed coding agents and wires up the MCP server automatically. Open any repo in your agent and orgraph starts working — it indexes on first run, no manual setup needed.

To undo: `orgraph uninstall`

## What agents can do

Once configured, your agent has 6 tools:

| Tool | What it does |
|---|---|
| `search(query)` | Hybrid BM25+semantic search — find code by description |
| `trace(symbol, direction, depth)` | Follow call chains forward (`callees`) or backward (`callers`) |
| `get_context(file_or_symbol)` | Topology cluster, community, call depth, indegree — where does this fit? |
| `find_entry_points(kind)` | HTTP handlers and entry surfaces; `kind = "all" \| "http" \| "topology"` |
| `get_dependencies(file, direction, depth)` | Import + call dependency tree, forward or reverse |
| `reindex(force)` | Re-index changed/deleted files without restarting the server |

The agent picks the right tool automatically based on what you ask.

## Manual usage

All commands take a repo path as the last argument. It defaults to `.` so you can omit it when you're already inside the repo.

### Setup

```bash
# Build the index (run once, then again after big merges)
orgraph index .

# Verify the index is healthy — node/edge counts, clusters, communities
orgraph status .
```

### Finding code

```bash
# Find code by describing what it does — semantic + keyword search
orgraph search "coupon validation logic" .
orgraph search "order cancellation refund" . --top-k 5

# List every function and class defined in a file (table of contents)
orgraph file controllers/DiscountController.py .

# See all HTTP endpoints and async tasks in the repo
orgraph entry-points .                    # HTTP handlers (default)
orgraph entry-points . --kind tasks       # Celery async tasks
orgraph entry-points . --kind all         # both together
```

### Understanding a function before you change it

```bash
# Who calls this function? (blast radius before editing)
orgraph who-calls get_valid_coupon .
orgraph who-calls build_order_model . --depth 2   # callers of callers too

# What does this function call? (trace the flow downward)
orgraph trace apply_coupon .
orgraph trace Coupon.on_post . --depth 3          # 3 levels deep
orgraph trace apply_coupon . --callers            # same as who-calls, tree form

# Architectural position — how central is this, what's coupled to it?
orgraph context controllers/DiscountController.py .
orgraph context get_valid_coupon .                # works on symbol names too
```

`context` shows call depth, indegree (how many things call this), which files are tightly coupled to it, and which functions tend to change together.

### Understanding file dependencies

```bash
# What does this file import? (what to read before editing it)
orgraph deps controllers/DiscountController.py .

# What imports this file? (what breaks if you delete or move it)
orgraph deps controllers/DiscountController.py . --direction imported_by

# Two levels of transitive imports
orgraph deps libs/OrderHelper.py . --depth 2
```

`deps` operates on `import` statements, not function calls — it shows module-level coupling, not runtime behaviour.

### Typical workflow on a new codebase

```bash
# 1. Index it
orgraph index .

# 2. See all the entry points
orgraph entry-points .

# 3. Find where something lives
orgraph search "payment processing" .

# 4. See what's in that file
orgraph file controllers/OrderController.py .

# 5. Before touching a function — check blast radius
orgraph who-calls build_order_model .

# 6. Trace what it does
orgraph trace build_order_model . --depth 3

# 7. Check how central the file is
orgraph context controllers/OrderController.py .
```

### MCP server

```bash
orgraph serve /path/to/repo
```

## Manual MCP config

If you prefer to configure manually instead of using `orgraph install`:

**Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "orgraph": {
      "command": "uvx",
      "args": ["--from", "orgraph-mcp", "orgraph", "serve", "."],
      "type": "stdio"
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "orgraph": {
      "command": "uvx",
      "args": ["--python", "3.13", "--from", "orgraph-mcp", "orgraph", "serve", "."]
    }
  }
}
```

The server uses `.` as the repo path — it starts relative to wherever your agent opens the project.

## How it works

- **Extraction** — tree-sitter AST extractor (SCIP compiler-accurate extraction when available)
- **Graph** — Kuzu embedded graph DB with Function/Class/File nodes + CALLS/IMPORTS/INHERITS edges
- **Search** — semble hybrid BM25 + Model2Vec embeddings
- **Topology** — BFS entry-point clustering groups files into domain clusters
- **Communities** — Leiden community detection for finer-grained groupings
- **Incremental** — md5 manifest tracks changes; `reindex` only re-extracts what changed

## Eval

Measure retrieval quality against a ground truth file:

```bash
orgraph eval /path/to/repo --ground-truth queries.json --output report.json
```

Ground truth format:
```json
[
  {
    "id": "auth-flow",
    "query": "how is authentication handled",
    "relevant_files": ["auth.py", "middleware.py"],
    "relevant_symbols": ["authenticate", "require_auth"],
    "query_type": "semantic"
  }
]
```

Reports NDCG@10, MRR, and Precision@3.
