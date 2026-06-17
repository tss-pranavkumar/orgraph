"""Pure graph query functions — the single source of truth for all Kuzu queries.

Both the CLI and MCP tools call these. Neither embeds raw Kuzu query strings.
All functions take an OrgraphDB as their first argument and return plain dicts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orgraph.graph.kuzu import OrgraphDB

# ── Enclosing symbol ──────────────────────────────────────────────────────────

def get_enclosing_symbol(db: OrgraphDB, file_path: str, line: int) -> dict[str, Any] | None:
    """Find the function or class whose definition encloses `line` in `file_path`.

    Returns {uid, name, kind, line} or None if nothing is found.
    Uses the nearest definition line at or before `line`.
    """
    best: dict[str, Any] | None = None
    best_line = -1
    for label, kind in (("Function", "function"), ("Class", "class")):
        rows = db.query_to_dicts(
            f"MATCH (s:{label}) WHERE s.path = $path AND s.line_number <= $line "
            "RETURN s.uid AS uid, s.name AS name, s.line_number AS def_line "
            "ORDER BY s.line_number DESC LIMIT 1",
            {"path": file_path, "line": line},
        )
        if rows and rows[0]["def_line"] > best_line:
            best_line = rows[0]["def_line"]
            best = {**rows[0], "kind": kind, "line": rows[0]["def_line"]}
    return best


# ── Symbol resolution ─────────────────────────────────────────────────────────

def resolve_symbol(db: OrgraphDB, name: str) -> list[dict[str, Any]]:
    """Find a symbol by exact name, falling back to CONTAINS.

    Searches Function first, then Class. Returns [{uid, name, path, line}].
    """
    for exact in (True, False):
        for label in ("Function", "Class"):
            q = (
                f"MATCH (s:{label}) WHERE s.name = $name "
                "RETURN s.uid AS uid, s.name AS name, s.path AS path, s.line_number AS line LIMIT 5"
                if exact else
                f"MATCH (s:{label}) WHERE s.name CONTAINS $name "
                "RETURN s.uid AS uid, s.name AS name, s.path AS path, s.line_number AS line LIMIT 3"
            )
            rows = db.query_to_dicts(q, {"name": name})
            if rows:
                return rows
    return []


def lookup_symbol_by_uid(db: OrgraphDB, uid: str) -> dict[str, Any] | None:
    """Return {uid, name, path, line, kind} for a known uid, or None."""
    for label, kind in (("Function", "function"), ("Class", "class")):
        rows = db.query_to_dicts(
            f"MATCH (s:{label}) WHERE s.uid = $uid "
            "RETURN s.uid AS uid, s.name AS name, s.path AS path, s.line_number AS line LIMIT 1",
            {"uid": uid},
        )
        if rows:
            return {**rows[0], "kind": kind}
    return None


# ── Call graph traversal ──────────────────────────────────────────────────────

def get_call_edges(db: OrgraphDB, uid: str, direction: str) -> list[dict[str, Any]]:
    """Single-hop CALLS edges from/to uid.

    direction: 'callees' (what uid calls) or 'callers' (what calls uid).
    Returns [{uid, name, path, line, call_kind, confidence}].
    Falls back gracefully if call_kind column is absent (pre-v0.1.20 indexes).
    """
    if direction == "callees":
        q = ("MATCH (f)-[r:CALLS]->(c) WHERE f.uid = $uid "
             "RETURN c.uid AS uid, c.name AS name, c.path AS path, "
             "c.line_number AS line, r.call_kind AS call_kind, r.confidence AS confidence LIMIT 30")
        q_fallback = ("MATCH (f)-[r:CALLS]->(c) WHERE f.uid = $uid "
                      "RETURN c.uid AS uid, c.name AS name, c.path AS path, "
                      "c.line_number AS line, r.confidence AS confidence LIMIT 30")
    else:
        q = ("MATCH (c)-[r:CALLS]->(f) WHERE f.uid = $uid "
             "RETURN c.uid AS uid, c.name AS name, c.path AS path, "
             "c.line_number AS line, r.call_kind AS call_kind, r.confidence AS confidence, "
             "r.line_number AS call_line LIMIT 30")
        q_fallback = ("MATCH (c)-[r:CALLS]->(f) WHERE f.uid = $uid "
                      "RETURN c.uid AS uid, c.name AS name, c.path AS path, "
                      "c.line_number AS line, r.confidence AS confidence, "
                      "r.line_number AS call_line LIMIT 30")
    try:
        return db.query_to_dicts(q, {"uid": uid})
    except Exception:
        return db.query_to_dicts(q_fallback, {"uid": uid})


def traverse_calls(
    db: OrgraphDB,
    uid: str,
    direction: str,
    depth: int,
) -> list[dict[str, Any]]:
    """BFS traversal of the call graph from uid.

    Returns a flat list of edge dicts, each with a 'depth' field.
    Each entry: {from_symbol, from_file, from_line, to_symbol, to_file, to_line,
                 call_kind, confidence, depth}.
    """
    depth = min(depth, 5)
    root_rows = db.query_to_dicts(
        "MATCH (s) WHERE s.uid = $uid RETURN s.name AS name, s.path AS path, s.line_number AS line LIMIT 1",
        {"uid": uid},
    )
    root_name = root_rows[0]["name"] if root_rows else uid
    root_path = root_rows[0]["path"] if root_rows else ""
    root_line = root_rows[0]["line"] if root_rows else 0

    chain: list[dict[str, Any]] = []
    visited: set[str] = {uid}
    frontier: list[tuple[str, str, str, int, int]] = [(uid, root_name, root_path, root_line, 0)]

    while frontier:
        cur_uid, cur_name, cur_path, cur_line, d = frontier.pop(0)
        if d >= depth:
            continue
        for e in get_call_edges(db, cur_uid, direction):
            chain.append({
                "from_symbol": cur_name,
                "from_file": cur_path,
                "from_line": cur_line,
                "to_symbol": e["name"],
                "to_file": e.get("path") or "",
                "to_line": e.get("line") or 0,
                "call_kind": e.get("call_kind") or "local",
                "confidence": e.get("confidence") or "INFERRED",
                "call_line": e.get("call_line") or 0,
                "depth": d + 1,
            })
            if e["uid"] not in visited:
                visited.add(e["uid"])
                frontier.append((e["uid"], e["name"], e.get("path") or "", e.get("line") or 0, d + 1))

    return chain[:100]


# ── File / symbol queries ─────────────────────────────────────────────────────

def resolve_file_path(db: OrgraphDB, file_path: str, repo_path: Path | None = None) -> str:
    """Resolve a relative path, filename, or fragment to an indexed absolute path.

    Returns the resolved path, or the original string if nothing matches.
    """
    candidate = Path(file_path)
    if not candidate.is_absolute() and repo_path:
        candidate = repo_path / file_path
    abs_candidate = str(candidate.resolve()) if candidate.exists() else ""

    if abs_candidate:
        rows = db.query_to_dicts(
            "MATCH (f:File {path: $path}) RETURN f.path AS path LIMIT 1",
            {"path": abs_candidate},
        )
        if rows:
            return rows[0]["path"]

    rows = db.query_to_dicts(
        "MATCH (f:File) WHERE f.name = $name RETURN f.path AS path LIMIT 1",
        {"name": Path(file_path).name},
    )
    if rows:
        return rows[0]["path"]

    rows = db.query_to_dicts(
        "MATCH (f:File) WHERE f.path CONTAINS $frag RETURN f.path AS path LIMIT 1",
        {"frag": file_path},
    )
    return rows[0]["path"] if rows else abs_candidate


def get_file_symbols(
    db: OrgraphDB,
    file_path: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """All functions and classes in file_path, ordered by line number.

    Returns [{uid, name, kind, path, line}].
    """
    rows: list[dict[str, Any]] = []
    for label, kind in (("Function", "function"), ("Class", "class")):
        part = db.query_to_dicts(
            f"MATCH (s:{label}) WHERE s.path = $path "
            "RETURN s.uid AS uid, s.name AS name, s.path AS path, s.line_number AS line",
            {"path": file_path},
        )
        for r in part:
            rows.append({
                "uid": r.get("uid") or "",
                "name": r.get("name") or "",
                "kind": kind,
                "path": r.get("path") or "",
                "line": r.get("line") or 0,
            })
    rows.sort(key=lambda r: (r["line"], r["kind"], r["name"]))
    return rows[:limit]


def get_symbol_indegree(db: OrgraphDB, uid: str) -> int:
    """Number of CALLS edges pointing at uid."""
    rows = db.query_to_dicts(
        "MATCH (caller)-[:CALLS]->(target) WHERE target.uid = $uid RETURN count(*) AS n",
        {"uid": uid},
    )
    return rows[0]["n"] if rows else 0


# ── Graph statistics ──────────────────────────────────────────────────────────

_NODE_LABELS = ("Function", "Class", "File", "Module", "Interface", "Struct", "Enum", "Variable")
_EDGE_RELATIONS = ("CALLS", "IMPORTS", "INHERITS", "CONTAINS", "IMPLEMENTS")


def get_node_counts(db: OrgraphDB) -> dict[str, int]:
    """Count of each node label that has at least one node."""
    counts: dict[str, int] = {}
    for label in _NODE_LABELS:
        try:
            rows = db.query_to_dicts(f"MATCH (n:{label}) RETURN count(n) AS cnt")
            cnt = rows[0]["cnt"] if rows else 0
        except Exception:
            cnt = 0
        if cnt > 0:
            counts[label] = cnt
    return counts


def get_edge_counts(db: OrgraphDB) -> dict[str, int]:
    """Count of each edge relation that has at least one edge."""
    counts: dict[str, int] = {}
    for rel in _EDGE_RELATIONS:
        try:
            rows = db.query_to_dicts(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS cnt")
            cnt = rows[0]["cnt"] if rows else 0
        except Exception:
            cnt = 0
        if cnt > 0:
            counts[rel] = cnt
    return counts


# ── Entry points ──────────────────────────────────────────────────────────────

def get_http_handlers(db: OrgraphDB) -> list[dict[str, Any]]:
    """All HTTP handler functions with method and route."""
    return db.query_to_dicts(
        "MATCH (f:Function) WHERE f.http_method <> '' "
        "RETURN f.name AS name, f.path AS path, f.line_number AS line, "
        "f.http_method AS http_method, f.http_path AS http_path LIMIT 100",
    )


def get_celery_dispatches(db: OrgraphDB) -> list[dict[str, Any]]:
    """All Celery dispatch call edges (caller → task)."""
    try:
        return db.query_to_dicts(
            "MATCH (caller)-[r:CALLS]->(callee) "
            "WHERE r.call_kind = 'celery_dispatch' "
            "RETURN caller.name AS caller, caller.path AS caller_path, "
            "caller.line_number AS caller_line, callee.name AS task, "
            "callee.path AS task_path, callee.line_number AS task_line, "
            "r.line_number AS line LIMIT 100",
        )
    except Exception:
        return []


# ── Dependency traversal ──────────────────────────────────────────────────────

def get_dependencies(
    db: OrgraphDB,
    file_path: str,
    direction: str,
    depth: int,
) -> list[dict[str, Any]]:
    """Import/call dependency tree for file_path.

    direction: 'imports' (outgoing) or 'imported_by' (incoming).
    depth: max traversal hops (capped at 3).
    Returns [{from_file, name, path, alias, transitive}].
    """
    depth = min(depth, 3)
    deps: list[dict[str, Any]] = []
    visited: set[str] = {file_path}
    frontier: list[tuple[str, int]] = [(file_path, 0)]

    while frontier:
        cur_path, d = frontier.pop(0)
        if d >= depth:
            continue

        if direction == "imports":
            for r in db.query_to_dicts(
                "MATCH (f:File {path: $path})-[r:IMPORTS]->(m:Module) "
                "RETURN m.name AS name, m.path AS mpath, r.alias AS alias LIMIT 50",
                {"path": cur_path},
            ):
                target = r.get("mpath") or r.get("name") or ""
                deps.append({
                    "from_file": cur_path, "name": r.get("name") or "",
                    "path": target, "alias": r.get("alias") or "", "transitive": d > 0,
                })
                if target and target not in visited:
                    visited.add(target)
                    frontier.append((target, d + 1))

            for r in db.query_to_dicts(
                "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
                "WHERE caller.path = $path AND callee.path <> $path "
                "RETURN DISTINCT callee.path AS dep_path LIMIT 30",
                {"path": cur_path},
            ):
                dep = r.get("dep_path") or ""
                if dep and dep not in visited:
                    visited.add(dep)
                    deps.append({
                        "from_file": cur_path, "name": Path(dep).name,
                        "path": dep, "alias": "", "transitive": d > 0,
                    })
                    frontier.append((dep, d + 1))
        else:
            for r in db.query_to_dicts(
                "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
                "WHERE callee.path = $path AND caller.path <> $path "
                "RETURN DISTINCT caller.path AS dep_path LIMIT 30",
                {"path": cur_path},
            ):
                dep = r.get("dep_path") or ""
                if dep and dep not in visited:
                    visited.add(dep)
                    deps.append({
                        "from_file": dep, "name": Path(dep).name,
                        "path": cur_path, "alias": "", "transitive": d > 0,
                    })
                    frontier.append((dep, d + 1))

    return deps[:100]
