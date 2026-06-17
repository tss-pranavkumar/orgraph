"""RepoContext — minimal shim that topology.py expects, built from ExtractionResult.

topology.py (lifted from codewiki) expects a `scan` object with:
  - scan.call_graph: CallGraph
  - scan.file_summaries: dict[str, str]
  - scan.entry_points: list[str]
  - scan.endpoint_bundles: list with .handler_file
  - scan.runtime_scan: object with .tasks[].file_path and .schedulers[].file_path
  - scan.parsed_files: dict[str, ParsedFile] where ParsedFile has .symbols[].name
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from orgraph.extract.types import ExtractionResult
from orgraph.topology.call_graph import (
    CALL_KIND_LOCAL,
    CallEdge,
    CallGraph,
    GraphRelation,
    REL_KIND_DEFINES,
)


@dataclass
class Symbol:
    name: str
    kind: str = "function"
    start_line: int = 0
    end_line: int = 0


@dataclass
class ParsedFile:
    symbols: list[Symbol] = field(default_factory=list)
    language: str = ""
    imports: list[str] = field(default_factory=list)


@dataclass
class _Task:
    file_path: str


@dataclass
class _RuntimeScan:
    tasks: list[_Task] = field(default_factory=list)
    schedulers: list[_Task] = field(default_factory=list)


@dataclass
class _EndpointBundle:
    handler_file: str


@dataclass
class RepoContext:
    """Adapter that makes ExtractionResult look like RepoScan for topology.py."""
    call_graph: CallGraph
    file_summaries: dict[str, str]
    entry_points: list[str]
    endpoint_bundles: list[_EndpointBundle]
    runtime_scan: _RuntimeScan
    parsed_files: dict[str, ParsedFile]


def build_repo_context(result: ExtractionResult, repo_path: Path) -> RepoContext:
    """Build a RepoContext from an ExtractionResult for topology analysis."""
    # Build uid → node lookup
    uid_to_node: dict[str, dict] = {n["uid"]: n for n in result.nodes}

    # Build CallGraph from CALLS edges
    cg = CallGraph()
    file_nodes: dict[str, list[dict]] = defaultdict(list)

    for node in result.nodes:
        path = node.get("path", "")
        if path:
            file_nodes[path].append(node)

    for node in result.nodes:
        path = node.get("path", "")
        name = node.get("name", "")
        if not path or not name:
            continue
        file_node = cg.file_node(path)
        sym_node = cg.symbol_node(path, name)
        cg.add_relation(GraphRelation(src=file_node, dst=sym_node, kind=REL_KIND_DEFINES))

    for edge in result.edges:
        if edge.get("relation") != "CALLS":
            continue
        src_node = uid_to_node.get(edge["source_uid"])
        dst_node = uid_to_node.get(edge["target_uid"])
        if not src_node or not dst_node:
            continue
        caller_file = src_node.get("path", "")
        caller_sym = src_node.get("name", "")
        callee_file = dst_node.get("path", "")
        callee_sym = dst_node.get("name", "")
        if caller_file and caller_sym and callee_file and callee_sym:
            call_kind = edge.get("call_kind") or CALL_KIND_LOCAL
            cg.add_edge(CallEdge(
                caller_file=caller_file,
                caller_symbol=caller_sym,
                callee_file=callee_file,
                callee_symbol=callee_sym,
                call_kind=call_kind if call_kind else CALL_KIND_LOCAL,
                call_site_line=edge.get("line_number", 0),
            ))

    # Build parsed_files from nodes grouped by file
    parsed_files: dict[str, ParsedFile] = {}
    for path, nodes in file_nodes.items():
        symbols = []
        for n in nodes:
            label = n.get("label", "Function")
            kind = "function" if label == "Function" else "class"
            symbols.append(Symbol(
                name=n.get("name", ""),
                kind=kind,
                start_line=n.get("line_number", 0),
                end_line=n.get("end_line", 0),
            ))
        rel_path = str(Path(path).relative_to(repo_path)) if path.startswith(str(repo_path)) else path
        parsed_files[path] = ParsedFile(symbols=symbols)
        # Also index by relative path since some callers use relative paths
        if rel_path != path:
            parsed_files[rel_path] = ParsedFile(symbols=symbols)

    # file_summaries: all unique file paths → empty string (enough for topology)
    all_paths = set(file_nodes.keys())
    file_summaries = {p: "" for p in all_paths}

    # entry_points: HTTP handler nodes (have http_method set)
    entry_points: list[str] = []
    for node in result.nodes:
        if node.get("http_method") and node.get("path"):
            fp = node["path"]
            if fp not in entry_points:
                entry_points.append(fp)

    return RepoContext(
        call_graph=cg,
        file_summaries=file_summaries,
        entry_points=entry_points,
        endpoint_bundles=[_EndpointBundle(handler_file=ep) for ep in entry_points],
        runtime_scan=_RuntimeScan(),
        parsed_files=parsed_files,
    )
