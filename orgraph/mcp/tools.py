"""MCP tool implementations for orgraph.

All tools accept an optional `repo` argument (absolute path to the project).
If omitted, the server falls back to the repo it was started with (if any).

This lets orgraph run as a single global MCP server shared across all projects,
matching semble's pattern of passing `repo` per tool call.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class State:
    """Mutable server state — updated in-place by reindex."""
    db: Any
    idx: Any
    topology: Any
    communities: dict[int, list[str]] | None
    repo_path: Path

    uid_to_community: dict[str, int] = field(default_factory=dict)
    cluster_by_id: dict[str, Any] = field(default_factory=dict)

    def rebuild_lookups(self) -> None:
        self.uid_to_community = {}
        if self.communities:
            for cid, nodes in self.communities.items():
                for uid in nodes:
                    self.uid_to_community[uid] = cid
        self.cluster_by_id = (
            {c.cluster_id: c for c in self.topology.clusters}
            if self.topology else {}
        )


# ── Module-level repo state cache ────────────────────────────────────────────
# Maps resolved repo path string → State.  Populated lazily per call.
_repo_states: dict[str, State] = {}
_repo_lock = threading.Lock()
_startup_repo: Path | None = None  # set by server.py; default when repo="" in tool calls

_LOADING: dict = {"status": "orgraph is indexing this repo — try again in a moment"}
_NOT_READY: dict = {"status": "orgraph is not ready. Run `orgraph index <repo>` first or wait for auto-index to complete."}


def _resolve_repo(repo_arg: str) -> Path | None:
    if repo_arg:
        return Path(repo_arg).expanduser().resolve()
    return _startup_repo


def _bg_load(state: State, repo_path: Path) -> None:
    """Ensure index exists, then load DB/search/topology/communities into state."""
    try:
        _ensure_indexed(repo_path)
        _load_into_state(state, repo_path)
    except Exception as exc:
        import sys
        print(f"orgraph: background load failed for {repo_path.name}: {exc}", file=sys.stderr)


def _ensure_indexed(repo_path: Path) -> None:
    import sys
    orgraph_dir = repo_path / ".orgraph"
    db_path = orgraph_dir / "graph.kuzu"
    if db_path.exists() and not db_path.is_dir():
        db_path.unlink()
    if db_path.exists():
        return
    print(f"orgraph: no index found for {repo_path.name} — indexing now…", file=sys.stderr)
    from click.testing import CliRunner
    from orgraph.cli import index
    result = CliRunner().invoke(index, [str(repo_path)])
    if result.exit_code != 0:
        print(f"orgraph: auto-index failed\n{result.output}", file=sys.stderr)


def _load_into_state(state: State, repo_path: Path) -> None:
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.search.index import SearchIndex
    from orgraph.topology.serialise import load_communities, load_topology

    orgraph_dir = repo_path / ".orgraph"
    db_path = orgraph_dir / "graph.kuzu"
    if not db_path.exists():
        return
    state.db = OrgraphDB(db_path)
    state.idx = SearchIndex.load(repo_path)
    state.topology = load_topology(orgraph_dir)
    state.communities = load_communities(orgraph_dir)
    state.rebuild_lookups()


def _get_state(repo_arg: str) -> State | None:
    """Return the State for the given repo, loading it if needed."""
    repo_path = _resolve_repo(repo_arg)
    if repo_path is None:
        return None
    key = str(repo_path)
    with _repo_lock:
        if key not in _repo_states:
            state = State(db=None, idx=None, topology=None, communities=None, repo_path=repo_path)
            state.rebuild_lookups()
            _repo_states[key] = state
            threading.Thread(target=_bg_load, args=(state, repo_path), daemon=True).start()
    return _repo_states[key]


def _no_repo_error(repo_arg: str) -> dict:
    return {
        "error": (
            "No repo specified and no default configured. "
            "Pass `repo` as the absolute path to your project. "
            f"Example: repo='/Users/you/my-project'"
        )
    }


def register_tools(mcp, startup_repo: Path | None = None) -> dict[str, Any]:
    """Register all orgraph tools on a FastMCP instance.

    startup_repo: if provided, pre-warms the state cache for that repo in background.
    Returns a dict of {tool_name: fn} for direct invocation in tests.
    """
    global _startup_repo
    _startup_repo = startup_repo

    # Pre-warm startup repo immediately
    if startup_repo:
        _get_state("")  # triggers background load for startup_repo

    # ── Tool 1: search ──────────────────────────────────────────────────────

    @mcp.tool()
    def search(query: str, repo: str = "", top_k: int = 10) -> list[dict[str, Any]]:
        """Hybrid BM25+semantic search over code chunks in this repo.

        Returns ranked results with file location and a code snippet.
        Use this to find relevant functions, classes, or logic by description.
        Pass `repo` as the absolute path to the project (e.g. repo='/path/to/project').
        """
        state = _get_state(repo)
        if state is None:
            return [_no_repo_error(repo)]
        if state.db is None:
            return [_LOADING]
        if state.idx is None:
            return [{"error": "Search index not built. Re-run `orgraph index`."}]
        results = state.idx.search(query, top_k=top_k)
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
        repo: str = "",
        direction: str = "callees",
        depth: int = 3,
    ) -> dict[str, Any]:
        """Trace the call chain from a function or class symbol.

        direction: 'callees' (what this symbol calls) or 'callers' (what calls it).
        depth: how many hops to follow (max 5).
        Pass `repo` as the absolute path to the project.
        """
        state = _get_state(repo)
        if state is None:
            return _no_repo_error(repo)
        if state.db is None:
            return _LOADING
        depth = min(depth, 5)

        roots = state.db.query_to_dicts(
            "MATCH (f:Function) WHERE f.name = $name "
            "RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 5",
            {"name": symbol},
        )
        if not roots:
            roots = state.db.query_to_dicts(
                "MATCH (c:Class) WHERE c.name = $name "
                "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line LIMIT 5",
                {"name": symbol},
            )
        if not roots:
            roots = state.db.query_to_dicts(
                "MATCH (f:Function) WHERE f.name CONTAINS $name "
                "RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 3",
                {"name": symbol},
            )
        if not roots:
            roots = state.db.query_to_dicts(
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
                edges = state.db.query_to_dicts(
                    "MATCH (f)-[r:CALLS]->(c) WHERE f.uid = $uid "
                    "RETURN c.uid AS uid, c.name AS name, c.path AS path, "
                    "c.line_number AS line, r.confidence AS confidence LIMIT 30",
                    {"uid": uid},
                )
            else:
                edges = state.db.query_to_dicts(
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
            "chain": chain[:100],
        }

    # ── Tool 3: get_context ─────────────────────────────────────────────────

    @mcp.tool()
    def get_context(file_or_symbol: str, repo: str = "") -> dict[str, Any]:
        """Return architectural context for a file path or symbol name.

        Looks up topology cluster, Leiden community, call depth, and indegree.
        Pass `repo` as the absolute path to the project.
        """
        state = _get_state(repo)
        if state is None:
            return _no_repo_error(repo)
        if state.db is None:
            return _LOADING

        file_path: str | None = None
        uid: str | None = None

        if "/" in file_or_symbol or "\\" in file_or_symbol or (
            "." in Path(file_or_symbol).name
        ):
            file_path = file_or_symbol
            candidate = Path(file_or_symbol)
            if not candidate.is_absolute():
                candidate = state.repo_path / file_or_symbol
            if candidate.exists():
                file_path = str(candidate.resolve())
        else:
            rows = state.db.query_to_dicts(
                "MATCH (f:Function) WHERE f.name = $name "
                "RETURN f.path AS path, f.uid AS uid LIMIT 1",
                {"name": file_or_symbol},
            )
            if not rows:
                rows = state.db.query_to_dicts(
                    "MATCH (c:Class) WHERE c.name = $name "
                    "RETURN c.path AS path, c.uid AS uid LIMIT 1",
                    {"name": file_or_symbol},
                )
            if rows:
                file_path = rows[0]["path"]
                uid = rows[0]["uid"]
            else:
                return {"query": file_or_symbol, "found": False}

        if not state.topology or not file_path:
            return {"query": file_or_symbol, "found": False}

        cluster_id = state.topology.file_cluster_id.get(file_path)
        cluster = state.cluster_by_id.get(cluster_id) if cluster_id else None

        community_id_for_file: int | None = None
        if uid:
            community_id_for_file = state.uid_to_community.get(uid)
        if community_id_for_file is None:
            rows = state.db.query_to_dicts(
                "MATCH (f:Function) WHERE f.path = $path RETURN f.uid AS uid LIMIT 20",
                {"path": file_path},
            )
            for row in rows:
                cid = state.uid_to_community.get(row["uid"])
                if cid is not None:
                    community_id_for_file = cid
                    break

        if uid:
            indegree_rows = state.db.query_to_dicts(
                "MATCH (caller)-[:CALLS]->(target) WHERE target.uid = $uid RETURN count(*) AS n",
                {"uid": uid},
            )
            indegree = indegree_rows[0]["n"] if indegree_rows else 0
        else:
            indegree = state.topology.file_indegree.get(file_path, 0)

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
            "call_depth": state.topology.file_call_depth.get(file_path),
            "indegree": indegree,
        }
        if cluster:
            result["cluster_related_files"] = [
                f for f in cluster.all_files[:10] if f != file_path
            ]
        return result

    # ── Tool 4: find_entry_points ───────────────────────────────────────────

    @mcp.tool()
    def find_entry_points(kind: str = "all", repo: str = "") -> list[dict[str, Any]]:
        """Return detected entry points grouped by topology cluster.

        kind: 'all' | 'http' (HTTP handlers only) | 'tasks' (async tasks only).
        Pass `repo` as the absolute path to the project.
        """
        state = _get_state(repo)
        if state is None:
            return [_no_repo_error(repo)]
        if state.db is None:
            return [_LOADING]
        if not state.topology:
            return [{"error": "No topology data. Re-run `orgraph index`."}]

        out: list[dict[str, Any]] = []

        if kind in ("all", "http"):
            rows = state.db.query_to_dicts(
                "MATCH (f:Function) WHERE f.http_method <> '' "
                "RETURN f.name AS name, f.path AS path, f.line_number AS line, "
                "f.http_method AS method, f.http_path AS route LIMIT 100",
            )
            for r in rows:
                cluster_id = state.topology.file_cluster_id.get(r["path"])
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
            for cluster in state.topology.clusters:
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
            for f, depth in state.topology.file_call_depth.items():
                if depth == 0 and state.topology.file_indegree.get(f, 0) == 0:
                    cluster_id = state.topology.file_cluster_id.get(f)
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
        repo: str = "",
        direction: str = "imports",
        depth: int = 2,
    ) -> dict[str, Any]:
        """Return the import/dependency tree for a file.

        direction: 'imports' (what this file imports) or 'imported_by' (reverse).
        depth: how many levels to traverse (max 3).
        Pass `repo` as the absolute path to the project.
        """
        state = _get_state(repo)
        if state is None:
            return _no_repo_error(repo)
        if state.db is None:
            return _LOADING
        depth = min(depth, 3)

        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = state.repo_path / file_path
        abs_path = str(candidate.resolve()) if candidate.exists() else file_path

        file_rows = state.db.query_to_dicts(
            "MATCH (f:File {path: $path}) RETURN f.path AS path LIMIT 1",
            {"path": abs_path},
        )
        if not file_rows:
            name = Path(file_path).name
            file_rows = state.db.query_to_dicts(
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
                rows = state.db.query_to_dicts(
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

                rows2 = state.db.query_to_dicts(
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

            else:
                rows = state.db.query_to_dicts(
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

    # ── Tool 6: reindex ─────────────────────────────────────────────────────

    @mcp.tool()
    def reindex(repo: str = "", force: bool = False) -> dict[str, Any]:
        """Re-index this repo to pick up new, modified, or deleted files.

        Detects changes via file-hash manifest — only re-extracts what changed.
        Use force=True to re-index everything.
        Pass `repo` as the absolute path to the project.
        """
        import time
        from orgraph.extract.manifest import Manifest
        from orgraph.extract.treesitter import TreeSitterExtractor
        from orgraph.graph.builder import GraphBuilder
        from orgraph.graph.schema import create_schema
        from orgraph.search.index import SearchIndex
        from orgraph.topology.cluster import build_nx_graph_from_result, cluster
        from orgraph.topology.context import build_repo_context
        from orgraph.topology.serialise import (
            load_communities, load_topology,
            save_communities, save_topology,
        )
        from orgraph.topology.topology import build_topology_map

        state = _get_state(repo)
        if state is None:
            return _no_repo_error(repo)
        if state.db is None:
            return _LOADING

        repo_path = state.repo_path
        orgraph_dir = repo_path / ".orgraph"
        t0 = time.perf_counter()

        manifest = Manifest(orgraph_dir)
        manifest.load()

        if force:
            changed = manifest.all_files(repo_path)
            deleted: list[str] = []
        else:
            changed = manifest.changed_files(repo_path)
            deleted = manifest.deleted_files(repo_path)

        if not changed and not deleted:
            return {"status": "up_to_date", "changed_files": 0, "deleted_files": 0}

        builder = GraphBuilder(db=state.db, repo_path=repo_path)

        deleted_nodes = 0
        for path_str in deleted:
            deleted_nodes += builder.delete_file_nodes(path_str)
        manifest.remove(deleted)

        nodes_added = 0
        if changed:
            for p in changed:
                builder.delete_file_nodes(str(p))

            ts = TreeSitterExtractor(repo_path=repo_path)
            from orgraph._vendor.extract import extract as _extract
            raw = _extract(changed, cache_root=None, parallel=True)
            result = ts._convert(raw)

            create_schema(state.db)
            nodes_added, _ = builder.ingest(result)

            ctx = build_repo_context(result, repo_path)
            topology = build_topology_map(ctx)
            full_ts = TreeSitterExtractor(repo_path=repo_path)
            full_raw = _extract(manifest.all_files(repo_path), cache_root=None, parallel=True)
            full_result = full_ts._convert(full_raw)
            G = build_nx_graph_from_result(full_result)
            communities = cluster(G)

            save_topology(topology, orgraph_dir)
            save_communities(communities, orgraph_dir)

            SearchIndex.build(repo_path)
            new_idx = SearchIndex.load(repo_path)

            state.idx = new_idx
            state.topology = topology
            state.communities = communities
            state.rebuild_lookups()

        manifest.update(changed)
        manifest.save()

        return {
            "status": "updated",
            "changed_files": len(changed),
            "deleted_files": len(deleted),
            "deleted_nodes": deleted_nodes,
            "nodes_added": nodes_added,
            "elapsed_s": round(time.perf_counter() - t0, 1),
        }

    return {
        "search": search,
        "trace": trace,
        "get_context": get_context,
        "find_entry_points": find_entry_points,
        "get_dependencies": get_dependencies,
        "reindex": reindex,
    }
