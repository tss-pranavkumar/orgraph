"""Parametrized call-graph correctness gate.

Runs the tree-sitter extractor over each ground-truth fixture and asserts recall
(true_edges present) and precision (forbidden_edges absent). Fixtures marked
`xfail` are expected-fail non-strictly, so the suite stays green while the
resolver is built; flipping a fixture's `xfail` to False turns it into a hard gate.
"""
from __future__ import annotations

import pytest

from orgraph.eval.callgraph_fixtures import FIXTURES, found_calls, write_fixture


@pytest.mark.parametrize("fx", FIXTURES, ids=lambda f: f.id)
def test_callgraph_pattern(fx, tmp_path, request):
    if fx.xfail:
        request.applymarker(pytest.mark.xfail(reason=fx.note or fx.id, strict=False))

    from orgraph.extract.treesitter import TreeSitterExtractor

    repo = write_fixture(fx, tmp_path)
    result = TreeSitterExtractor(repo).run()
    found = found_calls(result)

    missing = fx.true_edges - found
    leaked = fx.forbidden_edges & found
    assert not missing, f"[{fx.id}] missing edges: {sorted(missing)} | found: {sorted(found)}"
    assert not leaked, f"[{fx.id}] forbidden edges present: {sorted(leaked)}"


def test_callgraph_recall_baseline(tmp_path):
    """Aggregate scoreboard across the non-negative fixtures (printed on -s)."""
    from orgraph.extract.treesitter import TreeSitterExtractor

    total = hits = 0
    for fx in FIXTURES:
        if not fx.true_edges:
            continue
        repo = write_fixture(fx, tmp_path)
        found = found_calls(TreeSitterExtractor(repo).run())
        total += 1
        if fx.true_edges <= found:
            hits += 1
    print(f"\ncall-graph fixture recall: {hits}/{total} patterns")
    assert total > 0


def test_pyresolve_backs_off_on_ambiguous_reassignment(tmp_path):
    """A var reassigned to a different class must NOT yield a confidently-wrong
    resolved edge (regression for last-write-wins flow-insensitivity)."""
    from orgraph.extract.treesitter import TreeSitterExtractor

    (tmp_path / "m.py").write_text(
        "class A:\n    def run(self):\n        pass\n\n"
        "class B:\n    def run(self):\n        pass\n\n"
        "def f():\n    x = A()\n    x.run()\n    x = B()\n    x.run()\n"
    )
    result = TreeSitterExtractor(tmp_path).run()
    uid_name = {n["uid"]: n["name"] for n in result.nodes}
    resolved = {
        (uid_name.get(e["source_uid"]), uid_name.get(e["target_uid"]))
        for e in result.edges
        if e.get("relation") == "CALLS" and e.get("call_kind") == "resolved"
    }
    # ambiguous `x` → resolver must not emit a resolved edge to either A.run or B.run
    assert ("f", "A.run") not in resolved, f"wrong resolved edge: {resolved}"
    assert ("f", "B.run") not in resolved, f"wrong resolved edge: {resolved}"
