"""Shared index-build pipeline used by both `orgraph index` (CLI) and the `reindex` MCP tool.

Builds the full graph + topology + communities (+ optionally the search index) from a repo.

Why a full rebuild rather than delta patching: extraction is AST-cached (graphify cache keyed
by file hash), so unchanged files are not re-parsed and re-running is cheap. Rebuilding the whole
graph each time guarantees the result is byte-for-byte what a fresh `index` would produce — and
avoids the cross-file edge-loss that delta patching suffered from (deleting a changed file's nodes
dropped incoming CALLS edges from unchanged callers, which were never re-created).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orgraph.extract.types import ExtractionResult
from orgraph.graph.kuzu import OrgraphDB


def extract_repo(repo_path: Path, scratch_dir: Path | None = None) -> ExtractionResult:
    """Extract nodes/edges from a repo: SCIP if available, else tree-sitter."""
    from orgraph.extract.scip import ScipExtractor
    from orgraph.extract.treesitter import TreeSitterExtractor

    result = None
    if scratch_dir is not None:
        result = ScipExtractor(repo_path=repo_path, scratch_dir=scratch_dir).run()
    if result is not None:
        return result
    return TreeSitterExtractor(repo_path=repo_path).run()


def build_index(
    db: OrgraphDB,
    repo_path: Path,
    orgraph_dir: Path,
    *,
    rebuild_search: bool = True,
    result: ExtractionResult | None = None,
) -> dict[str, Any]:
    """Build the complete index into `db` and `.orgraph/`.

    Wipes and rebuilds the graph, then derives topology + communities from the same
    in-memory extraction result, persists them, and (optionally) rebuilds the search index.
    Caller owns the db lifecycle (open/close).
    """
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.schema import create_schema
    from orgraph.search.index import SearchIndex
    from orgraph.topology.cluster import build_nx_graph_from_result, cluster
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.serialise import save_communities, save_topology
    from orgraph.topology.topology import build_topology_map

    if result is None:
        result = extract_repo(repo_path, scratch_dir=orgraph_dir / "scip_scratch")

    warnings = _unextractable_warnings(repo_path, result)

    # --- Graph: full wipe + ingest ---
    create_schema(db)
    builder = GraphBuilder(db=db, repo_path=repo_path)
    builder.clear()
    nodes_written, edges_written = builder.ingest(result)

    # --- Topology + communities from the same extraction result ---
    ctx = build_repo_context(result, repo_path)
    topology = build_topology_map(ctx)
    communities = cluster(build_nx_graph_from_result(result))

    save_topology(topology, orgraph_dir)
    save_communities(communities, orgraph_dir)

    # --- Search index (full re-embed; semble has no incremental API) ---
    if rebuild_search:
        SearchIndex.build(repo_path)

    return {
        "extractor": result.extractor,
        "nodes": nodes_written,
        "edges": edges_written,
        "node_count": result.node_count(),
        "edge_count": result.edge_count(),
        "clusters": len(topology.clusters),
        "foundational": bool(topology.foundational_files),
        "communities": len(communities),
        "warnings": warnings,
        "topology": topology,
        "communities_map": communities,
    }


def _unextractable_warnings(repo_path: Path, result: ExtractionResult) -> list[str]:
    """Warn about code files that produced no symbols (missing grammar / scip binary).

    Compares the code files on disk against the file extensions that actually
    yielded nodes. Catches the silent "indexed 0 nodes" case — e.g. a Go repo
    when tree-sitter-go isn't installed.
    """
    from collections import Counter

    from orgraph.extract.treesitter import _walk_code_files

    produced = {Path(n.get("path", "")).suffix.lower() for n in result.nodes}
    missing: Counter[str] = Counter()
    for p in _walk_code_files(repo_path):
        suf = p.suffix.lower()
        if suf and suf not in produced:
            missing[suf] += 1
    if not missing:
        return []

    top = ", ".join(f"{count} {ext}" for ext, count in missing.most_common(6))
    return [
        f"No symbols extracted from {sum(missing.values())} code file(s) [{top}]. "
        "orgraph has no working extractor for these languages — install the matching "
        "tree-sitter grammar (e.g. `uv pip install tree-sitter-<lang>`) or a `scip-<lang>` "
        "binary, then re-index."
    ]
