"""Tests for Phase 5: eval harness."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "simple_python"
BUNDLED_GT = Path(__file__).parent.parent / "orgraph" / "eval" / "fixtures" / "codewiki_gt.json"


# ── metric unit tests ──────────────────────────────────────────────────────

def test_ndcg_perfect():
    from orgraph.eval.metrics import ndcg_at_k
    relevant = ["auth.py"]
    retrieved = ["/repo/auth.py", "/repo/models.py", "/repo/handlers.py"]
    score = ndcg_at_k(relevant, retrieved, k=3)
    assert score == pytest.approx(1.0)


def test_ndcg_zero():
    from orgraph.eval.metrics import ndcg_at_k
    score = ndcg_at_k(["auth.py"], ["/repo/models.py", "/repo/handlers.py"], k=3)
    assert score == pytest.approx(0.0)


def test_ndcg_second_rank():
    from orgraph.eval.metrics import ndcg_at_k
    relevant = ["auth.py"]
    retrieved = ["/repo/models.py", "/repo/auth.py", "/repo/handlers.py"]
    score = ndcg_at_k(relevant, retrieved, k=3)
    assert 0 < score < 1.0


def test_ndcg_empty_relevant():
    from orgraph.eval.metrics import ndcg_at_k
    assert ndcg_at_k([], ["/repo/auth.py"], k=5) == 0.0


def test_mrr_first():
    from orgraph.eval.metrics import mrr
    assert mrr(["auth.py"], ["/repo/auth.py", "/repo/models.py"]) == pytest.approx(1.0)


def test_mrr_second():
    from orgraph.eval.metrics import mrr
    assert mrr(["auth.py"], ["/repo/models.py", "/repo/auth.py"]) == pytest.approx(0.5)


def test_mrr_not_found():
    from orgraph.eval.metrics import mrr
    assert mrr(["auth.py"], ["/repo/models.py", "/repo/handlers.py"]) == pytest.approx(0.0)


def test_precision_at_k():
    from orgraph.eval.metrics import precision_at_k
    relevant = ["auth.py"]
    retrieved = ["/repo/auth.py", "/repo/models.py", "/repo/handlers.py"]
    assert precision_at_k(relevant, retrieved, k=3) == pytest.approx(1 / 3)


def test_precision_all_relevant():
    from orgraph.eval.metrics import precision_at_k
    relevant = ["auth.py", "models.py"]
    retrieved = ["/repo/auth.py", "/repo/models.py"]
    assert precision_at_k(relevant, retrieved, k=2) == pytest.approx(1.0)


def test_symbol_mrr_found():
    from orgraph.eval.metrics import symbol_mrr
    snippets = ["def authenticate(user):", "class User: pass"]
    assert symbol_mrr(["authenticate"], snippets) == pytest.approx(1.0)


def test_symbol_mrr_second():
    from orgraph.eval.metrics import symbol_mrr
    snippets = ["def get_user():", "def authenticate(user):"]
    assert symbol_mrr(["authenticate"], snippets) == pytest.approx(0.5)


# ── ground truth loading ───────────────────────────────────────────────────

def test_load_ground_truth_from_bundled():
    from orgraph.eval.ground_truth import load_ground_truth
    assert BUNDLED_GT.exists(), f"Bundled GT file missing: {BUNDLED_GT}"
    queries = load_ground_truth(BUNDLED_GT)
    assert len(queries) >= 20
    for q in queries:
        assert q.query
        assert isinstance(q.relevant_files, list)
        assert isinstance(q.relevant_symbols, list)
        assert q.query_type in ("semantic", "symbol", "trace")


def test_ground_truth_roundtrip(tmp_path):
    from orgraph.eval.ground_truth import EvalQuery, load_ground_truth, save_ground_truth
    queries = [
        EvalQuery(
            id="test_q",
            query="find authentication logic",
            relevant_files=["auth.py"],
            relevant_symbols=["authenticate"],
            query_type="semantic",
        )
    ]
    path = tmp_path / "gt.json"
    save_ground_truth(queries, path)
    loaded = load_ground_truth(path)
    assert len(loaded) == 1
    assert loaded[0].query == "find authentication logic"
    assert loaded[0].relevant_files == ["auth.py"]


# ── EvalRunner on fixture ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fixture_index(tmp_path_factory):
    """Build a minimal index over the simple_python fixture."""
    tmp = tmp_path_factory.mktemp("eval_index")
    target = tmp / "simple_python"
    shutil.copytree(FIXTURE, target)

    from orgraph.search.index import SearchIndex
    SearchIndex.build(target)
    return target


def test_eval_runner_completes(fixture_index, tmp_path):
    from orgraph.eval.ground_truth import EvalQuery, save_ground_truth
    from orgraph.eval.runner import EvalRunner

    gt = [
        EvalQuery(
            id="auth",
            query="authenticate user verify token",
            relevant_files=["auth.py"],
            relevant_symbols=["authenticate", "verify_token"],
            query_type="semantic",
        ),
        EvalQuery(
            id="models",
            query="user model order model",
            relevant_files=["models.py"],
            relevant_symbols=["User", "Order"],
            query_type="symbol",
        ),
    ]
    gt_path = tmp_path / "gt.json"
    save_ground_truth(gt, gt_path)

    runner = EvalRunner(repo_path=fixture_index, ground_truth_path=gt_path, top_k=5)
    report = runner.run()

    assert report.query_count == 2
    assert 0.0 <= report.ndcg_at_10 <= 1.0
    assert 0.0 <= report.mrr <= 1.0
    assert 0.0 <= report.precision_at_3 <= 1.0


def test_eval_report_has_per_query(fixture_index, tmp_path):
    from orgraph.eval.ground_truth import EvalQuery, save_ground_truth
    from orgraph.eval.runner import EvalRunner

    gt = [
        EvalQuery(
            id="q1",
            query="authenticate",
            relevant_files=["auth.py"],
            relevant_symbols=["authenticate"],
            query_type="semantic",
        )
    ]
    gt_path = tmp_path / "gt.json"
    save_ground_truth(gt, gt_path)

    runner = EvalRunner(repo_path=fixture_index, ground_truth_path=gt_path)
    report = runner.run()

    assert len(report.per_query) == 1
    assert report.per_query[0].query_id == "q1"
    assert isinstance(report.per_query[0].top_files, list)


def test_eval_report_save_roundtrip(fixture_index, tmp_path):
    from orgraph.eval.ground_truth import EvalQuery, save_ground_truth
    from orgraph.eval.runner import EvalRunner

    gt = [
        EvalQuery(
            id="q1",
            query="handlers create order",
            relevant_files=["handlers.py"],
            relevant_symbols=["create_order"],
            query_type="semantic",
        )
    ]
    gt_path = tmp_path / "gt.json"
    save_ground_truth(gt, gt_path)

    runner = EvalRunner(repo_path=fixture_index, ground_truth_path=gt_path)
    report = runner.run()

    out = tmp_path / "report.json"
    report.save(out)

    data = json.loads(out.read_text())
    assert "ndcg_at_10" in data
    assert "mrr" in data
    assert "query_count" in data
    assert data["query_count"] == 1


def test_eval_runner_raises_without_index(tmp_path):
    from orgraph.eval.ground_truth import EvalQuery, save_ground_truth
    from orgraph.eval.runner import EvalRunner

    gt = [EvalQuery(id="q1", query="test", relevant_files=[], relevant_symbols=[])]
    gt_path = tmp_path / "gt.json"
    save_ground_truth(gt, gt_path)

    with pytest.raises(RuntimeError, match="Search index not found"):
        EvalRunner(repo_path=tmp_path / "nonexistent", ground_truth_path=gt_path).run()
