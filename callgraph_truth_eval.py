"""Ground-truth call-graph scoreboard (manual runner).

Single source of truth = orgraph/eval/callgraph_fixtures.py FIXTURES.
Runs the FULL extractor (`.run()`, so the type-resolution pass is exercised) over
each fixture and prints per-pattern recall + forbidden-edge leaks for tree-sitter
(and SCIP when a binary is available).

    uv run python callgraph_truth_eval.py
"""
import tempfile
from pathlib import Path

from orgraph.eval.callgraph_fixtures import FIXTURES, found_calls, write_fixture
from orgraph.extract.treesitter import TreeSitterExtractor


def score(extractor_name: str, run) -> None:
    hits = total = 0
    leaks_total = 0
    print(f"\n=== {extractor_name} ===")
    for fx in FIXTURES:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_fixture(fx, Path(tmp))
            result = run(repo)
        found = found_calls(result) if result else set()
        missing = fx.true_edges - found
        leaked = fx.forbidden_edges & found
        ok = not missing and not leaked
        if fx.true_edges:
            total += 1
            hits += ok
        leaks_total += len(leaked)
        mark = "✓" if ok else "✗"
        tag = " (xfail)" if fx.xfail else ""
        print(f"  [{mark}] {fx.id:26}{tag}")
        if missing:
            print(f"        missing:   {sorted(missing)}")
        if leaked:
            print(f"        FORBIDDEN: {sorted(leaked)}")
    print(f"  recall: {hits}/{total} positive patterns | forbidden leaks: {leaks_total}")


if __name__ == "__main__":
    score("TREE-SITTER", lambda repo: TreeSitterExtractor(repo).run())

    from orgraph.extract.scip import ScipExtractor
    def _scip(repo: Path):
        return ScipExtractor(repo_path=repo, scratch_dir=repo / ".scip").run()
    score("SCIP", _scip)
