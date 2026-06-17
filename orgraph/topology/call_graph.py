"""Call graph data structures for orgraph topology analysis.

Lifted from codewiki/deepdoc/call_graph.py — data structures only.
The extraction from source text is not needed here; orgraph builds
the call graph directly from the ExtractionResult edges.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

# ── Edge kinds ────────────────────────────────────────────────────────────────
CALL_KIND_LOCAL = "local"
CALL_KIND_CELERY = "celery_dispatch"
CALL_KIND_SIGNAL = "signal_dispatch"
CALL_KIND_EVENT = "event_dispatch"
CALL_KIND_EXTERNAL = "external"

REL_KIND_IMPORTS = "imports"
REL_KIND_DEFINES = "defines"
REL_KIND_CONTAINS = "contains"
REL_KIND_DEFINED_IN = "defined_in"
REL_KIND_REFERENCES = "references"
REL_KIND_ROUTE_DECLARES = "route_declares"
REL_KIND_ROUTE_HANDLER = "route_handler"


@dataclass
class CallEdge:
    caller_file: str
    caller_symbol: str
    callee_file: str
    callee_symbol: str
    call_kind: str = CALL_KIND_LOCAL
    call_site_line: int = 0


@dataclass(frozen=True)
class GraphRelation:
    src: str
    dst: str
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallGraph:
    """Function-level call graph for a single repository."""

    _callees: dict[str, list[CallEdge]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _callers: dict[str, list[CallEdge]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _relations_out: dict[str, list[GraphRelation]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _relations_in: dict[str, list[GraphRelation]] = field(
        default_factory=lambda: defaultdict(list)
    )

    @staticmethod
    def _key(file_path: str, symbol: str) -> str:
        return f"{file_path}::{symbol}"

    @staticmethod
    def file_node(file_path: str) -> str:
        return f"file:{file_path}"

    @classmethod
    def symbol_node(cls, file_path: str, symbol: str) -> str:
        return f"symbol:{cls._key(file_path, symbol)}"

    def add_edge(self, edge: CallEdge) -> None:
        caller_key = self._key(edge.caller_file, edge.caller_symbol)
        callee_key = self._key(edge.callee_file, edge.callee_symbol)
        self._callees[caller_key].append(edge)
        self._callers[callee_key].append(edge)

    def add_relation(self, relation: GraphRelation) -> None:
        if relation in self._relations_out.get(relation.src, []):
            return
        self._relations_out[relation.src].append(relation)
        self._relations_in[relation.dst].append(relation)

    def get_callees(self, file_path: str, symbol: str) -> list[CallEdge]:
        return list(self._callees.get(self._key(file_path, symbol), []))

    def get_callers(self, file_path: str, symbol: str) -> list[CallEdge]:
        return list(self._callers.get(self._key(file_path, symbol), []))

    def get_outgoing_relations(
        self, node_id: str, *, kinds: set[str] | None = None
    ) -> list[GraphRelation]:
        relations = list(self._relations_out.get(node_id, []))
        if kinds is None:
            return relations
        return [r for r in relations if r.kind in kinds]

    def get_execution_chain(
        self, file_path: str, symbol: str, max_depth: int = 4, local_only: bool = True
    ) -> list[tuple[int, CallEdge]]:
        visited: set[str] = set()
        result: list[tuple[int, CallEdge]] = []
        queue: deque[tuple[str, str, int]] = deque()
        queue.append((file_path, symbol, 0))
        visited.add(self._key(file_path, symbol))
        while queue:
            cur_file, cur_sym, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self.get_callees(cur_file, cur_sym):
                if local_only and edge.call_kind == CALL_KIND_EXTERNAL:
                    continue
                result.append((depth + 1, edge))
                callee_key = self._key(edge.callee_file, edge.callee_symbol)
                if callee_key not in visited and edge.callee_file:
                    visited.add(callee_key)
                    queue.append((edge.callee_file, edge.callee_symbol, depth + 1))
        return result

    def files_in_chain(self, file_path: str, symbol: str, max_depth: int = 4) -> list[str]:
        chain = self.get_execution_chain(file_path, symbol, max_depth=max_depth)
        files: list[str] = []
        seen: set[str] = set()
        for _, edge in chain:
            if edge.callee_file and edge.callee_file not in seen:
                seen.add(edge.callee_file)
                files.append(edge.callee_file)
        return files

    def serialize(self) -> dict[str, Any]:
        seen: set[tuple] = set()
        edges = []
        for edge_list in self._callees.values():
            for e in edge_list:
                key = (e.caller_file, e.caller_symbol, e.callee_file, e.callee_symbol)
                if key not in seen:
                    seen.add(key)
                    edges.append({
                        "caller_file": e.caller_file,
                        "caller_symbol": e.caller_symbol,
                        "callee_file": e.callee_file,
                        "callee_symbol": e.callee_symbol,
                        "call_kind": e.call_kind,
                        "call_site_line": e.call_site_line,
                    })
        relations = []
        seen_rel: set[tuple[str, str, str]] = set()
        for relation_list in self._relations_out.values():
            for relation in relation_list:
                key = (relation.src, relation.dst, relation.kind)
                if key in seen_rel:
                    continue
                seen_rel.add(key)
                relations.append({
                    "src": relation.src,
                    "dst": relation.dst,
                    "kind": relation.kind,
                    "metadata": relation.metadata,
                })
        return {"edges": edges, "relations": relations, "version": 2}

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> CallGraph:
        g = cls()
        for e in data.get("edges", []):
            g.add_edge(CallEdge(**e))
        for r in data.get("relations", []):
            g.add_relation(GraphRelation(**r))
        return g

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.serialize(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CallGraph:
        return cls.deserialize(json.loads(path.read_text(encoding="utf-8")))

    def __len__(self) -> int:
        return sum(len(v) for v in self._callees.values())

    def stats(self) -> dict[str, int]:
        all_edges = [e for edges in self._callees.values() for e in edges]
        return {
            "total_edges": len(all_edges),
            "local": sum(1 for e in all_edges if e.call_kind == CALL_KIND_LOCAL),
            "external": sum(1 for e in all_edges if e.call_kind == CALL_KIND_EXTERNAL),
        }
