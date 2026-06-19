"""Shared data contracts for extraction output."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TypedDict


class NodeDict(TypedDict, total=False):
    uid: str            # md5(name + path + str(line_number))
    label: str          # "Function"|"Class"|"File"|"Module"|"Enum"|"Struct"|"Variable"|"Interface"
    name: str
    path: str           # absolute path
    line_number: int
    end_line: int
    lang: str
    source: str
    docstring: str
    is_dependency: bool
    confidence: str     # "EXTRACTED" | "INFERRED"
    http_method: str    # for HTTP handlers
    http_path: str


class EdgeDict(TypedDict, total=False):
    source_uid: str
    target_uid: str
    relation: str       # "CALLS"|"IMPORTS"|"INHERITS"|"CONTAINS"|"IMPLEMENTS"
    confidence: str
    line_number: int
    call_kind: str      # for CALLS: "local"|"celery_dispatch"|...
    alias: str          # for IMPORTS
    src_path: str       # for IMPORTS: importing file's abs path (File→File roll-up)
    dst_path: str       # for IMPORTS: imported symbol's defining file abs path


@dataclass
class ExtractionResult:
    nodes: list[NodeDict] = field(default_factory=list)
    edges: list[EdgeDict] = field(default_factory=list)
    extractor: str = "unknown"   # "scip" | "treesitter"

    def node_count(self) -> int:
        return len(self.nodes)

    def edge_count(self) -> int:
        return len(self.edges)


def make_uid(name: str, path: str, line_number: int) -> str:
    key = f"{name}::{path}::{line_number}"
    return hashlib.md5(key.encode()).hexdigest()
