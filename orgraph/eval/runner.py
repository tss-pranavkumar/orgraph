"""EvalRunner — runs the full retrieval eval pipeline and produces a report."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from orgraph.eval.ground_truth import EvalQuery, load_ground_truth
from orgraph.eval.metrics import mrr, ndcg_at_k, precision_at_k, symbol_mrr


@dataclass
class QueryResult:
    query_id: str
    query: str
    query_type: str
    ndcg_at_10: float
    mrr: float
    precision_at_3: float
    symbol_mrr: float
    top_files: list[str]        # top-5 retrieved file paths
    top_snippets: list[str]     # top-5 retrieved snippets


@dataclass
class EvalReport:
    ndcg_at_10: float
    mrr: float
    precision_at_3: float
    symbol_mrr: float
    query_count: int
    semantic_ndcg: float        # NDCG for semantic queries only
    symbol_query_mrr: float     # MRR for symbol queries only
    per_query: list[QueryResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ndcg_at_10": round(self.ndcg_at_10, 4),
            "mrr": round(self.mrr, 4),
            "precision_at_3": round(self.precision_at_3, 4),
            "symbol_mrr": round(self.symbol_mrr, 4),
            "query_count": self.query_count,
            "semantic_ndcg": round(self.semantic_ndcg, 4),
            "symbol_query_mrr": round(self.symbol_query_mrr, 4),
            "per_query": [
                {
                    "id": r.query_id,
                    "query": r.query,
                    "type": r.query_type,
                    "ndcg@10": round(r.ndcg_at_10, 4),
                    "mrr": round(r.mrr, 4),
                    "p@3": round(r.precision_at_3, 4),
                    "sym_mrr": round(r.symbol_mrr, 4),
                    "top_files": r.top_files[:3],
                }
                for r in self.per_query
            ],
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


class EvalRunner:
    def __init__(self, repo_path: Path, ground_truth_path: Path, top_k: int = 10) -> None:
        self.repo_path = repo_path
        self.ground_truth_path = ground_truth_path
        self.top_k = top_k

    def run(self) -> EvalReport:
        from orgraph.search.index import SearchIndex

        queries = load_ground_truth(self.ground_truth_path)
        idx = SearchIndex.load(self.repo_path)
        if idx is None:
            raise RuntimeError(
                f"Search index not found at {self.repo_path}. "
                "Run `orgraph index` first."
            )

        per_query: list[QueryResult] = []

        for q in queries:
            results = idx.search(q.query, top_k=self.top_k)
            retrieved_files = [r.chunk.file_path for r in results]
            retrieved_snippets = [r.chunk.content for r in results]

            qr = QueryResult(
                query_id=q.id or q.query[:40],
                query=q.query,
                query_type=q.query_type,
                ndcg_at_10=ndcg_at_k(q.relevant_files, retrieved_files, k=10),
                mrr=mrr(q.relevant_files, retrieved_files),
                precision_at_3=precision_at_k(q.relevant_files, retrieved_files, k=3),
                symbol_mrr=symbol_mrr(q.relevant_symbols, retrieved_snippets),
                top_files=retrieved_files[:5],
                top_snippets=[s[:200] for s in retrieved_snippets[:5]],
            )
            per_query.append(qr)

        def _mean(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        semantic = [r for r in per_query if r.query_type == "semantic"]
        symbol_qs = [r for r in per_query if r.query_type == "symbol"]

        return EvalReport(
            ndcg_at_10=_mean([r.ndcg_at_10 for r in per_query]),
            mrr=_mean([r.mrr for r in per_query]),
            precision_at_3=_mean([r.precision_at_3 for r in per_query]),
            symbol_mrr=_mean([r.symbol_mrr for r in per_query]),
            query_count=len(per_query),
            semantic_ndcg=_mean([r.ndcg_at_10 for r in semantic]),
            symbol_query_mrr=_mean([r.mrr for r in symbol_qs]),
            per_query=per_query,
        )
