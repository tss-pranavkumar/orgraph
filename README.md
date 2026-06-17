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

```bash
# Index a repo manually (optional — serve auto-indexes)
orgraph index /path/to/repo

# Check what was indexed
orgraph status /path/to/repo

# Search from the CLI
orgraph search "authentication middleware" /path/to/repo

# Start the MCP server
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
      "args": ["--from", "orgraph-mcp", "orgraph", "serve", "."]
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
      "args": ["--from", "orgraph-mcp", "orgraph", "serve", "."]
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
