"""orgraph web visualizer — a local, MIT-licensed code-graph browser.

Serves the pre-built `.orgraph/graph.kuzu` graph as JSON over a stdlib HTTP
server plus a single-page force-graph frontend. No new dependencies: uses
`http.server` and the existing `query.py` / `OrgraphDB` layer.

Run via `orgraph serve-web <repo>`; open the printed URL.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from orgraph.graph.kuzu import OrgraphDB

_STATIC_DIR = Path(__file__).parent / "web_static"

# Symbol node tables we surface in the graph (matches query._SYMBOL_KINDS intent).
_SYMBOL_LABELS = ("Function", "Class", "Interface", "Struct", "Enum")

# Soft caps: symbols by indegree, file nodes by symbol count.
_MAX_NODES = 6000
_MAX_FILE_NODES = 1500


def _q(db: OrgraphDB, query: str, params: dict | None = None) -> list[dict]:
    try:
        return db.query_to_dicts(query, params or {})
    except Exception:
        return []


def _rel_path(path: str, repo: str) -> str:
    if path and path.startswith(repo):
        return path[len(repo):].lstrip("/")
    return path or ""


class GraphData:
    """Loads + caches the whole symbol graph once, for fast repeated serving.

    Copies the Kuzu DB to a temp dir ONCE at startup (Kuzu takes an exclusive
    lock even in read_only mode, so reading a copy lets a concurrent `orgraph
    index` keep writing the original). One shared connection serves every
    request, serialized under `_lock` (Kuzu connections aren't thread-safe for
    concurrent execute, and ThreadingHTTPServer runs requests concurrently).
    """

    def __init__(self, db_path: Path, repo_root: Path) -> None:
        self.db_path = db_path
        self.repo_root = str(repo_root)
        self._lock = threading.Lock()
        self._graph: dict | None = None
        self._tmp: str | None = None
        self._db: OrgraphDB | None = None

    def _open(self) -> OrgraphDB:
        """Return the shared read-only handle, copying the DB once on first use."""
        if self._db is not None:
            return self._db
        self._tmp = tempfile.mkdtemp(prefix="orgraph_web_")
        tmp_db = Path(self._tmp) / "graph.kuzu"
        shutil.copytree(str(self.db_path), str(tmp_db))
        self._db = OrgraphDB(tmp_db)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def graph(self) -> dict:
        with self._lock:
            if self._graph is not None:
                return self._graph
            db = self._open()
            nodes: list[dict] = []
            nodes_by_kind: dict[str, int] = {}
            indeg: dict[str, int] = {}

            # indegree from CALLS — drives symbol node size.
            for r in _q(db, "MATCH (a)-[:CALLS]->(b) RETURN b.uid AS uid, count(*) AS c"):
                if r.get("uid"):
                    indeg[r["uid"]] = r["c"]

            # --- Symbol nodes (Function, Class, Interface, Struct, Enum) ---
            for label in _SYMBOL_LABELS:
                rows = _q(db, (
                    f"MATCH (n:{label}) "
                    "RETURN n.uid AS uid, n.name AS name, n.path AS path, "
                    "n.line_number AS line, n.lang AS lang, "
                    "n.community_id AS community, n.http_method AS http_method, "
                    "n.http_path AS http_path"
                ))
                for r in rows:
                    uid = r.get("uid")
                    if not uid:
                        continue
                    nodes.append({
                        "id": uid,
                        "name": r.get("name") or "?",
                        "kind": label,
                        "file": _rel_path(r.get("path") or "", self.repo_root),
                        "line": r.get("line") or 0,
                        "lang": r.get("lang") or "",
                        "community": r.get("community") or "",
                        "http_method": r.get("http_method") or "",
                        "http_path": r.get("http_path") or "",
                        "indegree": indeg.get(uid, 0),
                    })
                nodes_by_kind[label] = len(rows)

            truncated = False
            if len(nodes) > _MAX_NODES:
                nodes.sort(key=lambda n: n["indegree"], reverse=True)
                nodes = nodes[:_MAX_NODES]
                truncated = True
            keep_sym = {n["id"] for n in nodes}

            # --- File nodes — sized by symbol count ---
            sym_count: dict[str, int] = {}
            for label in _SYMBOL_LABELS:
                for r in _q(db, f"MATCH (n:{label}) RETURN n.path AS p, count(*) AS c"):
                    p = r.get("p")
                    if p:
                        sym_count[p] = sym_count.get(p, 0) + (r.get("c") or 0)

            file_rows = _q(db, "MATCH (f:File) RETURN f.path AS path, f.name AS name, f.lang AS lang")
            # Sort by symbol count desc, take top _MAX_FILE_NODES
            file_rows.sort(key=lambda r: sym_count.get(r.get("path") or "", 0), reverse=True)
            file_rows = file_rows[:_MAX_FILE_NODES]
            file_node_ids: set[str] = set()
            for r in file_rows:
                p = r.get("path")
                if not p:
                    continue
                file_node_ids.add(p)
                nodes.append({
                    "id": p,
                    "name": r.get("name") or p.split("/")[-1],
                    "kind": "File",
                    "file": _rel_path(p, self.repo_root),
                    "line": 0,
                    "lang": r.get("lang") or "",
                    "community": "",
                    "http_method": "",
                    "http_path": "",
                    "indegree": sym_count.get(p, 0),
                })
            nodes_by_kind["File"] = len(file_node_ids)

            keep = keep_sym | file_node_ids

            # --- Edges ---
            links: list[dict] = []

            # Symbol–symbol edges
            for rel in ("CALLS", "INHERITS", "IMPLEMENTS"):
                for r in _q(db, f"MATCH (a)-[:{rel}]->(b) RETURN a.uid AS s, b.uid AS t"):
                    s, t = r.get("s"), r.get("t")
                    if s in keep_sym and t in keep_sym and s != t:
                        links.append({"source": s, "target": t, "relation": rel})

            # File→File IMPORTS
            for r in _q(db, "MATCH (a:File)-[:IMPORTS]->(b:File) RETURN a.path AS s, b.path AS t"):
                s, t = r.get("s"), r.get("t")
                if s in file_node_ids and t in file_node_ids and s != t:
                    links.append({"source": s, "target": t, "relation": "IMPORTS"})

            # File→Symbol CONTAINS (so file nodes connect to their symbols)
            for label in _SYMBOL_LABELS:
                for r in _q(db, (
                    f"MATCH (f:File)-[:CONTAINS]->(s:{label}) "
                    "RETURN f.path AS s, s.uid AS t"
                )):
                    s, t = r.get("s"), r.get("t")
                    if s in file_node_ids and t in keep_sym:
                        links.append({"source": s, "target": t, "relation": "CONTAINS"})

            counts = {
                "nodes": len(nodes),
                "edges": len(links),
                "truncated": truncated,
                "nodes_by_kind": nodes_by_kind,
            }
            graph = {"nodes": nodes, "links": links, "stats": counts}
            self._graph = graph
            return graph

    def node_detail(self, uid: str) -> dict:
        from orgraph.graph import query as gq
        with self._lock:
            db = self._open()
            info = gq.lookup_symbol_by_uid(db, uid)
            if not info:
                return {"error": "not found"}
            callers = gq.get_call_edges(db, uid, "callers")
            callees = gq.get_call_edges(db, uid, "callees")
            # source snippet
            src = ""
            p = info.get("path") or ""
            ln = info.get("line") or 0
            try:
                lines = Path(p).read_text(errors="replace").splitlines()
                lo = max(0, ln - 1)
                src = "\n".join(lines[lo:lo + 25])
            except Exception:
                pass

            def _clean(rows: list[dict]) -> list[dict]:
                out = []
                for r in rows:
                    out.append({
                        "id": r.get("uid"),
                        "name": r.get("name") or "?",
                        "file": _rel_path(r.get("path") or "", self.repo_root),
                        "line": r.get("line") or 0,
                    })
                return out

            return {
                "id": uid,
                "name": info.get("name"),
                "kind": info.get("kind"),
                "file": _rel_path(p, self.repo_root),
                "line": ln,
                "source": src,
                "callers": _clean(callers),
                "callees": _clean(callees),
            }

    def search(self, term: str) -> list[dict]:
        if not term:
            return []
        with self._lock:
            db = self._open()
            out: list[dict] = []
            seen: set[str] = set()
            for label in _SYMBOL_LABELS:
                for r in _q(db, (
                    f"MATCH (n:{label}) WHERE n.name =~ $re "
                    "RETURN n.uid AS uid, n.name AS name, n.path AS path, "
                    "n.line_number AS line LIMIT 25"
                ), {"re": f"(?i).*{_escape_regex(term)}.*"}):
                    uid = r.get("uid")
                    if uid and uid not in seen:
                        seen.add(uid)
                        out.append({
                            "id": uid, "name": r.get("name") or "?",
                            "kind": label,
                            "file": _rel_path(r.get("path") or "", self.repo_root),
                            "line": r.get("line") or 0,
                        })
                if len(out) >= 50:
                    break
            return out[:50]

    def entrypoints(self) -> list[dict]:
        # Query directly (query.get_http_handlers omits uid, which we need to
        # highlight the matching graph nodes client-side).
        with self._lock:
            db = self._open()
            out = []
            for r in _q(db, (
                "MATCH (f:Function) WHERE f.http_method <> '' "
                "RETURN f.uid AS uid, f.name AS name, f.path AS path, "
                "f.line_number AS line, f.http_method AS http_method, "
                "f.http_path AS http_path ORDER BY f.path, f.line_number"
            )):
                out.append({
                    "id": r.get("uid"),
                    "name": r.get("name") or "?",
                    "http_method": r.get("http_method") or "",
                    "http_path": r.get("http_path") or "",
                    "file": _rel_path(r.get("path") or "", self.repo_root),
                    "line": r.get("line") or 0,
                })
            return out

    def tree(self) -> dict:
        """All indexed files (relative paths) + per-file symbol counts.

        The frontend nests these into an IDE-style folder tree. We include every
        File node — even ones with no extracted symbols (configs, docs) — so the
        tree mirrors the repo as it was indexed, not just the call graph.
        """
        with self._lock:
            db = self._open()
            counts: dict[str, int] = {}
            for label in _SYMBOL_LABELS:
                # Kuzu aggregates implicitly — no GROUP BY keyword.
                for r in _q(db, f"MATCH (n:{label}) RETURN n.path AS p, count(*) AS c"):
                    p = r.get("p")
                    if p:
                        counts[p] = counts.get(p, 0) + (r.get("c") or 0)
            files = []
            for r in _q(db, "MATCH (f:File) RETURN f.path AS p"):
                p = r.get("p")
                if not p:
                    continue
                files.append({"path": _rel_path(p, self.repo_root), "symbols": counts.get(p, 0)})
            files.sort(key=lambda x: x["path"])
            return {"files": files}

    def file_content(self, rel: str) -> dict:
        """Raw source of an indexed file + the symbols defined in it.

        Path is constrained to within the repo root (no traversal). Symbols are
        clickable in the UI → select the matching graph node.
        """
        from orgraph.graph import query as gq
        root = Path(self.repo_root).resolve()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)  # raises if rel escapes the repo
        except ValueError:
            return {"error": "path outside repo"}
        if not target.is_file():
            return {"error": "not a file"}
        try:
            content = target.read_text(errors="replace")
        except Exception as e:
            return {"error": str(e)}
        with self._lock:
            db = self._open()
            syms = []
            for s in gq.get_file_symbols(db, str(target)):
                syms.append({
                    "id": s.get("uid"), "name": s.get("name") or "?",
                    "kind": s.get("kind") or "", "line": s.get("line") or 0,
                })
        return {"path": rel, "content": content, "symbols": syms,
                "lines": content.count("\n") + 1}

    def path(self, frm: str, to: str) -> dict:
        from orgraph.graph import query as gq
        with self._lock:
            db = self._open()
            f_rows = gq.resolve_symbol(db, frm)
            t_rows = gq.resolve_symbol(db, to)
            if not f_rows or not t_rows:
                return {"status": "not_found", "reason": "symbol not resolved"}
            chain = gq.find_path(db, f_rows[0]["uid"], t_rows[0]["uid"])
            if not chain:
                return {"status": "no_path"}
            return {
                "status": "ok",
                "hops": [{
                    "id": h.get("uid"), "name": h.get("name"),
                    "file": _rel_path(h.get("path") or "", self.repo_root),
                    "line": h.get("line") or 0,
                } for h in chain],
            }


def _escape_regex(s: str) -> str:
    return "".join("\\" + c if c in r".^$*+?()[]{}|\\" else c for c in s)


def _make_handler(data: GraphData):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, obj, status=200, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            try:
                if u.path == "/" or u.path == "/index.html":
                    return self._serve_static("index.html")
                if u.path == "/sigma.min.js":
                    return self._serve_static("sigma.min.js", "application/javascript")
                if u.path == "/graphology.umd.min.js":
                    return self._serve_static("graphology.umd.min.js", "application/javascript")
                if u.path == "/graphology-layout-forceatlas2.min.js":
                    return self._serve_static("graphology-layout-forceatlas2.min.js", "application/javascript")
                if u.path == "/force-graph.min.js":
                    return self._serve_static("force-graph.min.js", "application/javascript")
                if u.path == "/api/graph":
                    return self._send(data.graph())
                if u.path == "/api/node":
                    return self._send(data.node_detail(qs.get("uid", [""])[0]))
                if u.path == "/api/search":
                    return self._send(data.search(qs.get("q", [""])[0]))
                if u.path == "/api/entrypoints":
                    return self._send(data.entrypoints())
                if u.path == "/api/tree":
                    return self._send(data.tree())
                if u.path == "/api/file":
                    return self._send(data.file_content(qs.get("path", [""])[0]))
                if u.path == "/api/path":
                    return self._send(data.path(qs.get("from", [""])[0], qs.get("to", [""])[0]))
                self._send({"error": "not found"}, 404)
            except Exception as e:  # never crash the server on a bad query
                self._send({"error": str(e)}, 500)

        def _serve_static(self, name: str, ctype: str = "text/html"):
            fp = _STATIC_DIR / name
            if not fp.exists():
                return self._send({"error": "missing static asset"}, 404)
            self._send(fp.read_bytes(), ctype=ctype)

    return Handler


def serve(repo_path: Path, host: str = "127.0.0.1", port: int = 4747) -> None:
    """Start the visualizer server for an indexed repo. Blocks until Ctrl-C."""
    repo = repo_path.resolve()
    db_path = repo / ".orgraph" / "graph.kuzu"
    if not db_path.exists():
        raise SystemExit(f"Not indexed: {db_path} missing. Run `orgraph index {repo}` first.")
    data = GraphData(db_path, repo)
    # warm the cache so first paint is instant and surfaces errors early
    g = data.graph()
    handler = _make_handler(data)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"  orgraph visualizer → {url}")
    print(f"  {g['stats']['nodes']} nodes · {g['stats']['edges']} edges"
          + ("  (truncated to top-indegree)" if g["stats"]["truncated"] else ""))
    print("  Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        httpd.shutdown()
    finally:
        data.close()
