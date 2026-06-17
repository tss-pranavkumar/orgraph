"""Ground truth data contracts for orgraph eval harness."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EvalQuery:
    """A single ground-truth eval query with expected retrieval targets."""
    query: str
    relevant_files: list[str]       # relative paths (suffix-matched against retrieved abs paths)
    relevant_symbols: list[str]     # function/class names expected in results
    query_type: str = "semantic"    # "semantic" | "symbol" | "trace"
    id: str = ""                    # optional stable ID for tracking per-query regressions


def load_ground_truth(path: Path) -> list[EvalQuery]:
    data = json.loads(path.read_text(encoding="utf-8"))
    queries = []
    for item in data:
        queries.append(EvalQuery(
            query=item["query"],
            relevant_files=item.get("relevant_files", []),
            relevant_symbols=item.get("relevant_symbols", []),
            query_type=item.get("query_type", "semantic"),
            id=item.get("id", ""),
        ))
    return queries


def save_ground_truth(queries: list[EvalQuery], path: Path) -> None:
    data = [
        {
            "id": q.id,
            "query": q.query,
            "relevant_files": q.relevant_files,
            "relevant_symbols": q.relevant_symbols,
            "query_type": q.query_type,
        }
        for q in queries
    ]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
