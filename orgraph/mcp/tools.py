"""MCP tool implementations for orgraph.

All tools are registered onto a FastMCP instance via register_tools().
Context (db, search index, topology, communities) is captured via closure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def register_tools(
    mcp,
    db,
    idx,
    topology,
    communities: dict[int, list[str]] | None,
    repo_path: Path,
) -> dict[str, Any]:
    """Register all 5 orgraph tools on a FastMCP instance.

    Returns a dict of {tool_name: fn} so callers (e.g. tests) can invoke
    tools directly without going through the FastMCP async layer.
    """

    # Reverse index: node uid → community id (built once at startup)
    uid_to_community: dict[str, int] = {}
    if communities:
        for cid, nodes in communities.items():
            for uid in nodes:
                uid_to_community[uid] = cid

    # Topology cluster lookup: file_path → cluster_id → TopologyCluster
    cluster_by_id = {c.cluster_id: c for c in topology.clusters} if topology else {}

    # ── Tool 1: search ──────────────────────────────────────────────────────

    @mcp.tool()
    def search(query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Hybrid BM25+semantic search over code chunks in this repo.

        Returns ranked results with file location and a code snippet.
        Use this to find relevant functions, classes, or logic by description.
        """
        if idx is None:
            return [{"error": "Search index not built. Re-run `orgraph index`."}]
        results = idx.search(query, top_k=top_k)
        out = []
        for r in results:
            c = r.chunk
            out.append({
                "file": c.file_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "snippet": c.content[:400],
                "score": round(r.score, 4),
                "language": c.language or "",
            })
        return out

    # ── Tool 2: trace ───────────────────────────────────────────────────────

    @mcp.tool()
    def trace(
        symbol: str,
        direction: str = "callees",
        depth: int = 3,
    ) -> dict[str, Any]:
        """Trace the call chain from a function or class symbol.

        direction: 'callees' (what this symbol calls) or 'callers' (what calls it).
        depth: how many hops to follow (max 5).
        Returns the root symbol and its call chain with file locations.
        """
        depth = min(depth, 5)

        # Find root nodes matching symbol name (Function first, then Class)
        roots = db.query_to_dicts(
            "MATCH (f:Function) WHERE f.name = $name "
            "RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 5",
            {"name": symbol},
        )
        if not roots:
            roots = db.query_to_dicts(
                "MATCH (c:Class) WHERE c.name = $name "
                "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line LIMIT 5",
                {"name": symbol},
            )
        if not roots:
            # Try substring match across both labels
            roots = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.name CONTAINS $name "
                "RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 3",
                {"name": symbol},
            )
        if not roots:
            roots = db.query_to_dicts(
                "MATCH (c:Class) WHERE c.name CONTAINS $name "
                "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line LIMIT 3",
                {"name": symbol},
            )
        if not roots:
            return {"root": symbol, "found": False, "chain": []}

        root = roots[0]
        chain: list[dict] = []
        visited: set[str] = {root["uid"]}
        frontier: list[tuple[str, str, str, int, int]] = [
            (root["uid"], root["name"], root["path"], root.get("line") or 0, 0)
        ]

        while frontier:
            uid, name, path, line, d = frontier.pop(0)
            if d >= depth:
                continue

            if direction == "callees":
                edges = db.query_to_dicts(
                    "MATCH (f)-[r:CALLS]->(c) WHERE f.uid = $uid "
                    "RETURN c.uid AS uid, c.name AS name, c.path AS path, "
                    "c.line_number AS line, r.confidence AS confidence LIMIT 30",
                    {"uid": uid},
                )
            else:
                edges = db.query_to_dicts(
                    "MATCH (c)-[r:CALLS]->(f) WHERE f.uid = $uid "
                    "RETURN c.uid AS uid, c.name AS name, c.path AS path, "
                    "c.line_number AS line, r.confidence AS confidence LIMIT 30",
                    {"uid": uid},
                )

            for e in edges:
                chain.append({
                    "from_symbol": name,
                    "from_file": path,
                    "from_line": line,
                    "to_symbol": e["name"],
                    "to_file": e["path"],
                    "to_line": e.get("line") or 0,
                    "confidence": e.get("confidence") or "INFERRED",
                    "depth": d + 1,
                })
                if e["uid"] not in visited:
                    visited.add(e["uid"])
                    frontier.append((e["uid"], e["name"], e["path"], e.get("line") or 0, d + 1))

        return {
            "root": root["name"],
            "root_file": root["path"],
            "root_line": root.get("line") or 0,
            "direction": direction,
            "found": True,
            "chain": chain[:100],  # cap to 100 edges
        }

    # ── Tool 3: get_context ─────────────────────────────────────────────────

    @mcp.tool()
    def get_context(file_or_symbol: str) -> dict[str, Any]:
        """Return architectural context for a file path or symbol name.

        Looks up which topology cluster owns it, which Leiden community
        it belongs to, related entry points, and call-depth information.
        Use this to understand where a file/symbol fits in the codebase.
        """
        file_path: str | None = None

        # Heuristic: if it has path separators or a file extension, treat as file
        if "/" in file_or_symbol or "\\" in file_or_symbol or (
            "." in Path(file_or_symbol).name
        ):
            file_path = file_or_symbol
            # Try to resolve to absolute path
            candidate = Path(file_or_symbol)
            if not candidate.is_absolute():
                candidate = repo_path / file_or_symbol
            if candidate.exists():
                file_path = str(candidate.resolve())
        else:
            # Symbol name — find its file from Kuzu (Function first, then Class)
            rows = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.name = $name "
                "RETURN f.path AS path, f.uid AS uid LIMIT 1",
                {"name": file_or_symbol},
            )
            if not rows:
                rows = db.query_to_dicts(
                    "MATCH (c:Class) WHERE c.name = $name "
                    "RETURN c.path AS path, c.uid AS uid LIMIT 1",
                    {"name": file_or_symbol},
                )
            if rows:
                file_path = rows[0]["path"]
                uid = rows[0]["uid"]
                community_id = uid_to_community.get(uid)
            else:
                return {"query": file_or_symbol, "found": False}

        if not topology or not file_path:
            return {"query": file_or_symbol, "found": False}

        # Cluster lookup
        cluster_id = topology.file_cluster_id.get(file_path)
        cluster = cluster_by_id.get(cluster_id) if cluster_id else None

        # Community lookup (for file: check all symbols in that file)
        community_id_for_file: int | None = None
        if file_path:
            rows = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.path = $path RETURN f.uid AS uid LIMIT 20",
                {"path": file_path},
            )
            for row in rows:
                cid = uid_to_community.get(row["uid"])
                if cid is not None:
                    community_id_for_file = cid
                    break

        # Symbol-level indegree: count incoming CALLS edges for this uid when available,
        # otherwise fall back to file-level indegree from topology.
        uid_for_indegree: str | None = locals().get("uid")  # set above if symbol path was taken
        if uid_for_indegree:
            indegree_rows = db.query_to_dicts(
                "MATCH (caller)-[:CALLS]->(target) WHERE target.uid = $uid RETURN count(*) AS n",
                {"uid": uid_for_indegree},
            )
            indegree = indegree_rows[0]["n"] if indegree_rows else 0
        else:
            indegree = topology.file_indegree.get(file_path, 0)

        result: dict[str, Any] = {
            "query": file_or_symbol,
            "file_path": file_path,
            "found": True,
            "cluster_id": cluster_id,
            "cluster_entry_files": cluster.entry_files[:5] if cluster else [],
            "cluster_file_count": len(cluster.all_files) if cluster else 0,
            "cluster_avg_indegree": round(cluster.avg_indegree, 2) if cluster else None,
            "is_foundational": cluster.is_foundational if cluster else False,
            "community_id": community_id_for_file,
            "call_depth": topology.file_call_depth.get(file_path),
            "indegree": indegree,
        }

        # Related files in same cluster
        if cluster:
            result["cluster_related_files"] = [
                f for f in cluster.all_files[:10] if f != file_path
            ]

        return result

    # ── Tool 4: find_entry_points ───────────────────────────────────────────

    @mcp.tool()
    def find_entry_points(kind: str = "all") -> list[dict[str, Any]]:
        """Return detected entry points grouped by topology cluster.

        kind: 'all' | 'http' (HTTP handlers only) | 'tasks' (async tasks only).
        Entry points are the outermost callable surfaces of the codebase —
        HTTP handlers, CLI commands, async task workers, etc.
        """
        if not topology:
            return [{"error": "No topology data. Re-run `orgraph index`."}]

        out: list[dict[str, Any]] = []

        if kind in ("all", "http"):
            # HTTP handlers from Kuzu (have http_method populated)
            rows = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.http_method <> '' "
                "RETURN f.name AS name, f.path AS path, f.line_number AS line, "
                "f.http_method AS method, f.http_path AS route LIMIT 100",
            )
            for r in rows:
                cluster_id = topology.file_cluster_id.get(r["path"])
                out.append({
                    "kind": "http",
                    "symbol": r["name"],
                    "file": r["path"],
                    "line": r.get("line") or 0,
                    "http_method": r.get("method") or "",
                    "http_path": r.get("route") or "",
                    "cluster": cluster_id,
                })

        if kind in ("all", "topology"):
            # BFS entry files from topology clusters (depth 0)
            for cluster in topology.clusters:
                if cluster.is_foundational:
                    continue
                for ef in cluster.entry_files[:3]:
                    out.append({
                        "kind": "topology_entry",
                        "file": ef,
                        "cluster": cluster.cluster_id,
                        "cluster_file_count": len(cluster.all_files),
                        "entry_symbols": cluster.entry_symbols[:5],
                    })

        if kind == "all" and not out:
            # Fallback: indegree-0 non-foundational files
            for f, depth in topology.file_call_depth.items():
                if depth == 0 and topology.file_indegree.get(f, 0) == 0:
                    cluster_id = topology.file_cluster_id.get(f)
                    out.append({
                        "kind": "indegree_zero",
                        "file": f,
                        "cluster": cluster_id,
                    })

        return out[:50]

    # ── Tool 5: get_dependencies ────────────────────────────────────────────

    @mcp.tool()
    def get_dependencies(
        file_path: str,
        direction: str = "imports",
        depth: int = 2,
    ) -> dict[str, Any]:
        """Return the import/dependency tree for a file.

        direction: 'imports' (what this file imports) or 'imported_by' (reverse).
        depth: how many levels to traverse (max 3).
        Uses the IMPORTS and CONTAINS graph edges for traversal.
        """
        depth = min(depth, 3)

        # Resolve to absolute path
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = repo_path / file_path
        abs_path = str(candidate.resolve()) if candidate.exists() else file_path

        # Check file exists in graph
        file_rows = db.query_to_dicts(
            "MATCH (f:File {path: $path}) RETURN f.path AS path LIMIT 1",
            {"path": abs_path},
        )
        if not file_rows:
            # Try by name match
            name = Path(file_path).name
            file_rows = db.query_to_dicts(
                "MATCH (f:File) WHERE f.name = $name RETURN f.path AS path LIMIT 1",
                {"name": name},
            )
            if file_rows:
                abs_path = file_rows[0]["path"]

        deps: list[dict[str, Any]] = []
        visited: set[str] = {abs_path}
        frontier: list[tuple[str, int]] = [(abs_path, 0)]

        while frontier:
            cur_path, d = frontier.pop(0)
            if d >= depth:
                continue

            if direction == "imports":
                # Direct imports via IMPORTS edges (File→Module)
                rows = db.query_to_dicts(
                    "MATCH (f:File {path: $path})-[r:IMPORTS]->(m:Module) "
                    "RETURN m.name AS name, m.path AS mpath, r.alias AS alias LIMIT 50",
                    {"path": cur_path},
                )
                for r in rows:
                    target = r.get("mpath") or r.get("name") or ""
                    deps.append({
                        "from_file": cur_path,
                        "name": r.get("name") or "",
                        "path": target,
                        "alias": r.get("alias") or "",
                        "transitive": d > 0,
                    })
                    if target and target not in visited:
                        visited.add(target)
                        frontier.append((target, d + 1))

                # Also: CALLS-based dependencies (functions this file calls in other files)
                rows2 = db.query_to_dicts(
                    "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
                    "WHERE caller.path = $path AND callee.path <> $path "
                    "RETURN DISTINCT callee.path AS dep_path LIMIT 30",
                    {"path": cur_path},
                )
                for r in rows2:
                    dep = r.get("dep_path") or ""
                    if dep and dep not in visited:
                        visited.add(dep)
                        deps.append({
                            "from_file": cur_path,
                            "name": Path(dep).name if dep else "",
                            "path": dep,
                            "alias": "",
                            "transitive": d > 0,
                        })
                        frontier.append((dep, d + 1))

            else:  # imported_by
                rows = db.query_to_dicts(
                    "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
                    "WHERE callee.path = $path AND caller.path <> $path "
                    "RETURN DISTINCT caller.path AS dep_path LIMIT 30",
                    {"path": cur_path},
                )
                for r in rows:
                    dep = r.get("dep_path") or ""
                    if dep and dep not in visited:
                        visited.add(dep)
                        deps.append({
                            "from_file": dep,
                            "name": Path(dep).name if dep else "",
                            "path": cur_path,
                            "alias": "",
                            "transitive": d > 0,
                        })
                        frontier.append((dep, d + 1))

        return {
            "file": abs_path,
            "direction": direction,
            "depth": depth,
            "deps": deps[:100],
            "dep_count": len(deps),
        }

    return {
        "search": search,
        "trace": trace,
        "get_context": get_context,
        "find_entry_points": find_entry_points,
        "get_dependencies": get_dependencies,
    }
