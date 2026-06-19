"""Ingests an ExtractionResult into the Kuzu graph."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from orgraph.extract.types import ExtractionResult
from orgraph.graph.kuzu import OrgraphDB

_log = logging.getLogger("orgraph.graph.builder")

_PARAM_RE = re.compile(r"\$(\w+)")


def _used_params(cypher: str, params: dict) -> dict:
    """Keep only the params a query references. Kuzu raises on any *unused* param,
    so e.g. the Class MERGE (which has no http_method) must not be handed one."""
    wanted = set(_PARAM_RE.findall(cypher))
    return {k: v for k, v in params.items() if k in wanted}

# Node labels that have line_number-based primary keys
_SYMBOL_LABELS = frozenset({"Function", "Class", "Interface", "Struct", "Enum", "Variable", "Module"})

# Edge relation → (from_labels, to_labels) — Kuzu requires explicit node table pairs.
# IMPORTS is special-cased in _write_edges (File→File matched by path, not uid).
# Only symbol-table (uid-keyed) pairs belong here — File/Directory CONTAINS edges are
# written separately in _write_file_nodes (File is path-keyed, has no `uid`, so a
# `MATCH (:File {uid})` would raise). Every pair below must be a valid schema FROM/TO
# pair, so a MATCH that finds nothing returns 0 rows (not an error) and an exception
# therefore unambiguously signals a real schema/column mismatch.
_EDGE_TABLES: dict[str, list[tuple[str, str]]] = {
    "CALLS":      [("Function", "Function"), ("Function", "Class"), ("Class", "Function")],
    "INHERITS":   [("Class", "Class"), ("Class", "Interface"), ("Interface", "Interface"), ("Struct", "Struct")],
    "CONTAINS":   [("Class", "Function")],
    "IMPLEMENTS": [("Class", "Interface"), ("Struct", "Interface")],
}

# Columns each rel table actually has (mirrors schema.py rel DDL). _write_edges
# SETs ONLY these per relation — never a column the table lacks. This is the fix
# for the IMPORTS confidence crash (IMPORTS has no `confidence` column, but the old
# code ran `SET r.confidence` for every relation → every IMPORTS write threw and was
# swallowed). Keep in lockstep with schema.py; drop-accounting catches drift.
_EDGE_COLUMNS: dict[str, tuple[str, ...]] = {
    "CALLS":      ("line_number", "confidence", "call_kind"),
    "IMPORTS":    ("line_number", "alias"),
    "INHERITS":   ("line_number", "confidence"),
    "IMPLEMENTS": ("confidence",),
    "CONTAINS":   (),
}


def _edge_set(relation: str, edge: dict) -> tuple[str, dict]:
    """Build the `SET r.col = $col, ...` clause for only the columns `relation` has."""
    available = {
        "line_number": edge.get("line_number", 0),
        "confidence":  edge.get("confidence", "INFERRED"),
        "call_kind":   edge.get("call_kind", "local"),
        "alias":       edge.get("alias", "") or "",
    }
    cols = _EDGE_COLUMNS.get(relation, ())
    if not cols:
        return "", {}
    clause = "SET " + ", ".join(f"r.{c} = ${c}" for c in cols)
    # .get() so a future column added to _EDGE_COLUMNS but not to `available`
    # degrades to a default rather than raising a KeyError (which would be
    # mis-accounted as a schema-error and drop the whole relation).
    return clause, {c: available.get(c, "") for c in cols}

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
        # Populated by ingest(); initialised here so callers can read it safely
        # even if ingest() returns early.
        self.last_ingest_summary: dict = {"nodes_written": 0, "extracted": {}, "persisted": {}, "drops": {}}

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
        """Write nodes then edges. Returns (nodes_written, edges_written).

        Detailed per-relation accounting (extracted vs persisted, drops by reason)
        is stored on `self.last_ingest_summary` so silent loss is observable and
        testable. A whole-relation drop (extracted > 0 but persisted == 0) or any
        `schema-error` is logged at WARNING.
        """
        node_count = self._write_nodes(result)
        self._write_file_nodes(result)
        edge_count, summary = self._write_edges(result)
        self.last_ingest_summary = {"nodes_written": node_count, **summary}
        self._warn_on_loss(summary)
        return node_count, edge_count

    @staticmethod
    def _warn_on_loss(summary: dict) -> None:
        extracted = summary.get("extracted", {})
        persisted = summary.get("persisted", {})
        drops = summary.get("drops", {})
        for rel, n in extracted.items():
            if n > 0 and persisted.get(rel, 0) == 0:
                _log.warning("orgraph: relation %s fully dropped on write (%d extracted, 0 persisted)", rel, n)
        for key, n in drops.items():
            if key.endswith(":schema-error") and n:
                _log.warning("orgraph: %d %s edge writes hit a schema error (column/table mismatch)", n, key.split(":")[0])

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

    def _write_edges(self, result: ExtractionResult) -> tuple[int, dict]:
        """Write edges with accurate persistence accounting.

        Returns (edges_persisted, summary). `summary` carries per-relation
        extracted/persisted counts and a `drops` breakdown by reason:
          - src-missing / dst-missing : edge had an empty endpoint uid
          - dst-missing-external      : import target's file is outside the graph (legit)
          - self-import               : a file importing itself (legit)
          - unresolved-external       : endpoint symbol not in the graph (stdlib/external — legit)
          - no-label-pair             : both endpoints exist but no rel FROM/TO pair allows it (BUG)
          - schema-error              : the write raised — column/table mismatch (BUG)
        """
        uid_to_path = {n["uid"]: n.get("path", "") for n in result.nodes if n.get("uid")}
        uid_set = set(uid_to_path)
        persisted: dict[str, int] = {}
        extracted: dict[str, int] = {}
        drops: dict[str, int] = {}

        def bump(d: dict, k: str) -> None:
            d[k] = d.get(k, 0) + 1

        def drop(relation: str, reason: str) -> None:
            bump(drops, f"{relation}:{reason}")

        count = 0
        for edge in result.edges:
            relation = edge.get("relation", "CALLS")
            bump(extracted, relation)
            set_clause, set_params = _edge_set(relation, edge)

            # IMPORTS is File→File, keyed by path (not uid) — handle before uid checks.
            if relation == "IMPORTS":
                ok, reason = self._write_import(edge, set_clause, set_params)
                if ok:
                    bump(persisted, relation); count += 1
                else:
                    drop(relation, reason)
                continue

            src_uid = edge.get("source_uid", "")
            dst_uid = edge.get("target_uid", "")
            if not src_uid:
                drop(relation, "src-missing"); continue
            if not dst_uid:
                drop(relation, "dst-missing"); continue

            pairs = _EDGE_TABLES.get(relation, [])
            written = schema_error = False
            for src_label, dst_label in pairs:
                cypher = (
                    f"MATCH (s:{src_label} {{uid: $src}}), (d:{dst_label} {{uid: $dst}}) "
                    f"MERGE (s)-[r:{relation}]->(d) {set_clause} RETURN count(r) AS c"
                )
                try:
                    rows = self.db.query_to_dicts(cypher, {"src": src_uid, "dst": dst_uid, **set_params})
                except Exception:
                    schema_error = True
                    break
                if rows and rows[0].get("c", 0) > 0:
                    written = True
                    break
            if written:
                bump(persisted, relation); count += 1
            elif schema_error:
                drop(relation, "schema-error")
            elif src_uid in uid_set and dst_uid in uid_set:
                drop(relation, "no-label-pair")   # both nodes exist but no FROM/TO pair allows the edge
            else:
                drop(relation, "unresolved-external")   # endpoint not in graph (stdlib/external) — legitimate
        return count, {"extracted": extracted, "persisted": persisted, "drops": drops}

    def _write_import(self, edge: dict, set_clause: str, set_params: dict) -> tuple[bool, str]:
        """Persist a File→File IMPORTS edge (both endpoints pre-resolved to paths).

        The extractor resolves the importing file (`src_path`) and the imported
        symbol's defining file (`dst_path`); we match both File nodes by path.
        """
        src_path = edge.get("src_path", "")
        dst_path = edge.get("dst_path", "")
        if not src_path:
            return False, "src-missing"
        if not dst_path:
            return False, "dst-missing-external"   # imported symbol defined outside the graph (stdlib/3rd-party)
        if src_path == dst_path:
            return False, "self-import"
        cypher = (
            "MATCH (s:File {path: $sp}), (d:File {path: $dp}) "
            f"MERGE (s)-[r:IMPORTS]->(d) {set_clause} RETURN count(r) AS c"
        )
        try:
            rows = self.db.query_to_dicts(cypher, {"sp": src_path, "dp": dst_path, **set_params})
        except Exception:
            return False, "schema-error"
        if rows and rows[0].get("c", 0) > 0:
            return True, ""
        return False, "unresolved-external"   # a File node for one side wasn't created
