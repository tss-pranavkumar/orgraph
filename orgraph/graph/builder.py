"""Ingests an ExtractionResult into the Kuzu graph."""
from __future__ import annotations

import re
from pathlib import Path

from orgraph.extract.types import ExtractionResult
from orgraph.graph.kuzu import OrgraphDB

_PARAM_RE = re.compile(r"\$(\w+)")


def _used_params(cypher: str, params: dict) -> dict:
    """Keep only the params a query references. Kuzu raises on any *unused* param,
    so e.g. the Class MERGE (which has no http_method) must not be handed one."""
    wanted = set(_PARAM_RE.findall(cypher))
    return {k: v for k, v in params.items() if k in wanted}

# Node labels that have line_number-based primary keys
_SYMBOL_LABELS = frozenset({"Function", "Class", "Interface", "Struct", "Enum", "Variable", "Module"})

# Edge relation → (from_labels, to_labels) — Kuzu requires explicit node table pairs
_EDGE_TABLES: dict[str, list[tuple[str, str]]] = {
    "CALLS":      [("Function", "Function"), ("Function", "Class"), ("Class", "Function")],
    "IMPORTS":    [("File", "Module")],
    "INHERITS":   [("Class", "Class"), ("Class", "Interface"), ("Interface", "Interface"), ("Struct", "Struct")],
    "CONTAINS":   [("File", "Function"), ("File", "Class"), ("File", "Enum"), ("File", "Struct"),
                   ("File", "Variable"), ("Directory", "File")],
    "IMPLEMENTS": [("Class", "Interface"), ("Struct", "Interface")],
}

# Cypher merge templates per node label
_MERGE_FUNCTION = """
MERGE (n:Function {uid: $uid})
SET n.name = $name, n.path = $path, n.line_number = $line_number,
    n.end_line = $end_line, n.lang = $lang, n.source = $source,
    n.docstring = $docstring, n.is_dependency = $is_dependency,
    n.confidence = $confidence, n.community_id = '', n.cluster_id = '',
    n.http_method = $http_method, n.http_path = $http_path
"""

_MERGE_CLASS = """
MERGE (n:Class {uid: $uid})
SET n.name = $name, n.path = $path, n.line_number = $line_number,
    n.end_line = $end_line, n.lang = $lang, n.source = $source,
    n.docstring = $docstring, n.is_dependency = $is_dependency,
    n.confidence = $confidence, n.community_id = '', n.cluster_id = ''
"""

_MERGE_INTERFACE = """
MERGE (n:Interface {uid: $uid})
SET n.name = $name, n.path = $path, n.line_number = $line_number,
    n.lang = $lang, n.is_dependency = $is_dependency, n.confidence = $confidence
"""

_MERGE_STRUCT = _MERGE_INTERFACE.replace("Interface", "Struct")
_MERGE_ENUM   = _MERGE_INTERFACE.replace("Interface", "Enum")
_MERGE_VAR    = _MERGE_INTERFACE.replace("Interface", "Variable")

_MERGE_FILE = """
MERGE (n:File {path: $path})
SET n.name = $name, n.relative_path = $relative_path,
    n.lang = $lang, n.is_dependency = false
"""

_MERGE_DIR = """
MERGE (n:Directory {path: $path})
SET n.name = $name
"""

_NODE_MERGES: dict[str, str] = {
    "Function": _MERGE_FUNCTION,
    "Class": _MERGE_CLASS,
    "Interface": _MERGE_INTERFACE,
    "Struct": _MERGE_STRUCT,
    "Enum": _MERGE_ENUM,
    "Variable": _MERGE_VAR,
    "File": _MERGE_FILE,
    "Directory": _MERGE_DIR,
}


def _node_params(node: dict) -> dict:
    label = node.get("label", "Function")
    base = {
        "uid": node.get("uid", ""),
        "name": node.get("name", ""),
        "path": node.get("path", ""),
        "line_number": node.get("line_number", 0),
        "end_line": node.get("end_line", 0),
        "lang": node.get("lang", ""),
        "source": node.get("source", ""),
        "docstring": node.get("docstring", "") or "",
        "is_dependency": node.get("is_dependency", False),
        "confidence": node.get("confidence", "EXTRACTED"),
        "http_method": node.get("http_method", "") or "",
        "http_path": node.get("http_path", "") or "",
    }
    if label in ("File",):
        path = Path(node.get("path", ""))
        base["name"] = path.name
        base["relative_path"] = node.get("relative_path", path.name)
    return base


class GraphBuilder:
    """Writes ExtractionResult nodes + edges into Kuzu."""

    def __init__(self, db: OrgraphDB, repo_path: Path) -> None:
        self.db = db
        self.repo_path = repo_path

    def clear(self) -> int:
        """Delete every node (DETACH removes all incident edges). Returns nodes deleted.

        Used for full graph rebuilds — guarantees no stale nodes/edges and no
        MERGE-duplicated edges survive across re-indexes.
        """
        total = 0
        for label in (
            "Function", "Class", "Interface", "Struct", "Enum",
            "Variable", "Module", "File", "Directory",
        ):
            try:
                rows = self.db.query_to_dicts(f"MATCH (n:{label}) RETURN count(n) AS c")
                total += rows[0]["c"] if rows else 0
                self.db.execute(f"MATCH (n:{label}) DETACH DELETE n")
            except Exception:
                pass
        return total

    def delete_file_nodes(self, file_path: str) -> int:
        """Delete all symbol nodes belonging to a file. Returns count deleted."""
        count = 0
        for label in ("Function", "Class", "Interface", "Struct", "Enum", "Variable"):
            try:
                rows = self.db.query_to_dicts(
                    f"MATCH (n:{label}) WHERE n.path = $path RETURN n.uid AS uid",
                    {"path": file_path},
                )
                for row in rows:
                    self.db.execute(
                        f"MATCH (n:{label} {{uid: $uid}}) DETACH DELETE n",
                        {"uid": row["uid"]},
                    )
                    count += 1
            except Exception:
                pass
        # delete the File node itself
        try:
            self.db.execute("MATCH (f:File {path: $path}) DETACH DELETE f", {"path": file_path})
        except Exception:
            pass
        return count

    def ingest(self, result: ExtractionResult) -> tuple[int, int]:
        """Write nodes then edges. Returns (nodes_written, edges_written)."""
        node_count = self._write_nodes(result)
        self._write_file_nodes(result)
        edge_count = self._write_edges(result)
        return node_count, edge_count

    def _write_nodes(self, result: ExtractionResult) -> int:
        count = 0
        for node in result.nodes:
            label = node.get("label", "Function")
            cypher = _NODE_MERGES.get(label)
            if not cypher:
                continue
            try:
                self.db.execute(cypher, _used_params(cypher, _node_params(node)))
                count += 1
            except Exception:
                pass  # skip duplicate or schema mismatch
        return count

    def _write_file_nodes(self, result: ExtractionResult) -> None:
        """Synthesise File and Directory nodes from the extraction paths."""
        seen_files: set[str] = set()
        seen_dirs: set[str] = set()

        for node in result.nodes:
            path_str = node.get("path", "")
            if not path_str:
                continue
            p = Path(path_str)

            # File + Directory nodes: once per unique path.
            if path_str not in seen_files:
                seen_files.add(path_str)
                rel = str(p.relative_to(self.repo_path)) if p.is_relative_to(self.repo_path) else p.name
                lang = node.get("lang", "")
                try:
                    self.db.execute(_MERGE_FILE, {
                        "path": path_str, "name": p.name,
                        "relative_path": rel, "lang": lang,
                    })
                except Exception:
                    pass

                dir_path = str(p.parent)
                if dir_path not in seen_dirs:
                    seen_dirs.add(dir_path)
                    try:
                        self.db.execute(_MERGE_DIR, {"path": dir_path, "name": p.parent.name})
                    except Exception:
                        pass

            # CONTAINS: File → symbol, for *every* symbol in the file (not just the first).
            if node.get("uid"):
                sym_label = node.get("label", "Function")
                if sym_label in ("Function", "Class", "Interface", "Enum", "Struct", "Variable"):
                    try:
                        cypher = (
                            f"MATCH (f:File {{path: $fp}}), (s:{sym_label} {{uid: $uid}}) "
                            f"MERGE (f)-[:CONTAINS]->(s)"
                        )
                        self.db.execute(cypher, {"fp": path_str, "uid": node["uid"]})
                    except Exception:
                        pass

    def _write_edges(self, result: ExtractionResult) -> int:
        count = 0
        for edge in result.edges:
            relation = edge.get("relation", "CALLS")
            src_uid = edge.get("source_uid", "")
            dst_uid = edge.get("target_uid", "")
            if not src_uid or not dst_uid:
                continue

            confidence = edge.get("confidence", "INFERRED")
            line_no = edge.get("line_number", 0)
            call_kind = edge.get("call_kind", "local")

            # Try each valid (src_label, dst_label) pair for this relation
            pairs = _EDGE_TABLES.get(relation, [])
            written = False
            for src_label, dst_label in pairs:
                try:
                    cypher = (
                        f"MATCH (s:{src_label} {{uid: $src}}), (d:{dst_label} {{uid: $dst}}) "
                        f"MERGE (s)-[r:{relation}]->(d) "
                        f"SET r.confidence = $conf, r.line_number = $line"
                    )
                    params = {
                        "src": src_uid, "dst": dst_uid,
                        "conf": confidence, "line": line_no,
                    }
                    if relation == "CALLS":
                        cypher += ", r.call_kind = $call_kind"
                        params["call_kind"] = call_kind
                    self.db.execute(cypher, params)
                    written = True
                    break
                except Exception:
                    continue
            if written:
                count += 1
        return count
