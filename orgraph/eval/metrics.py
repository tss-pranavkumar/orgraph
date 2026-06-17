"""Standard IR metrics for retrieval evaluation."""
from __future__ import annotations

import math


def _is_relevant(retrieved_path: str, relevant_files: list[str]) -> bool:
    """True if retrieved_path ends with any relevant file suffix (path-portable)."""
    rp = retrieved_path.replace("\\", "/")
    for rel in relevant_files:
        rel = rel.replace("\\", "/").lstrip("/")
        if rp.endswith(rel) or rel in rp:
            return True
    return False


def _relevance_list(retrieved: list[str], relevant_files: list[str]) -> list[int]:
    """Binary relevance list, crediting each relevant file at most once.

    Multiple retrieved chunks from the same file only earn one hit — otherwise
    DCG can exceed IDCG and produce NDCG > 1.
    """
    matched: set[str] = set()
    result: list[int] = []
    for r in retrieved:
        rp = r.replace("\\", "/")
        hit_rel: str | None = None
        for rel in relevant_files:
            rel_norm = rel.replace("\\", "/").lstrip("/")
            if (rp.endswith(rel_norm) or rel_norm in rp) and rel_norm not in matched:
                hit_rel = rel_norm
                break
        if hit_rel:
            matched.add(hit_rel)
            result.append(1)
        else:
            result.append(0)
    return result


def ndcg_at_k(relevant_files: list[str], retrieved: list[str], k: int = 10) -> float:
    """Normalised Discounted Cumulative Gain @ k.

    relevant_files: list of ground-truth file path suffixes
    retrieved: ordered list of retrieved file paths (most relevant first)
    """
    if not relevant_files or not retrieved:
        return 0.0

    rels = _relevance_list(retrieved[:k], relevant_files)

    def dcg(gains: list[int]) -> float:
        return sum(g / math.log2(i + 2) for i, g in enumerate(gains))

    actual_dcg = dcg(rels)
    # Ideal: all relevant docs at the top
    ideal_count = min(len(relevant_files), k)
    ideal_dcg = dcg([1] * ideal_count)

    return actual_dcg / ideal_dcg if ideal_dcg > 0 else 0.0


def mrr(relevant_files: list[str], retrieved: list[str]) -> float:
    """Mean Reciprocal Rank — reciprocal of the rank of the first relevant result."""
    for i, r in enumerate(retrieved):
        if _is_relevant(r, relevant_files):
            return 1.0 / (i + 1)
    return 0.0


def precision_at_k(relevant_files: list[str], retrieved: list[str], k: int = 3) -> float:
    """Fraction of top-k retrieved results that are relevant."""
    if not retrieved:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if _is_relevant(r, relevant_files))
    return hits / len(top_k)


def symbol_mrr(relevant_symbols: list[str], retrieved_snippets: list[str]) -> float:
    """MRR where relevance = any relevant symbol appears in the retrieved snippet."""
    if not relevant_symbols:
        return 0.0
    for i, snippet in enumerate(retrieved_snippets):
        for sym in relevant_symbols:
            if sym in snippet:
                return 1.0 / (i + 1)
    return 0.0
