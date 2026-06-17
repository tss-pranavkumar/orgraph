# orgraph

Codebase knowledge graph for coding agents. Points at any repo, builds a persistent graph, serves it via MCP.

## Install

```bash
uv venv && uv pip install -e .
```

## Usage

```bash
# Index a repo
orgraph index /path/to/repo

# Check graph stats
orgraph status /path/to/repo

# Search code
orgraph search "authentication middleware" /path/to/repo

# Start MCP server (add to Cursor / Claude CLI config)
orgraph serve /path/to/repo
```

## MCP config (Claude CLI)

```json
{
  "mcpServers": {
    "orgraph": { "command": "orgraph", "args": ["serve", "."] }
  }
}
```

## Architecture

See [plan](../.claude/plans/i-wnant-you-to-glowing-pelican.md) for full design.

- **Extraction**: SCIP (compiler-accurate) → tree-sitter fallback (graphify)
- **Storage**: Kuzu embedded graph DB
- **Search**: semble hybrid BM25 + Model2Vec
- **Topology**: codewiki BFS entry-point clustering
- **Communities**: Leiden community detection (graphify)
- **Agent interface**: FastMCP stdio server, 5 tools
