"""Tests for the SCIP parser (orgraph/extract/scip.py).

Runs against a committed SCIP index fixture (tests/fixtures/simple_python.scip,
generated once with scip-python) so CI needs no scip-python binary. A separate
guarded test exercises the live binary when it happens to be installed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURE_DIR = (Path(__file__).parent / "fixtures" / "simple_python").resolve()
FIXTURE_SCIP = Path(__file__).parent / "fixtures" / "simple_python.scip"


@pytest.fixture(scope="module")
def scip_result():
    from orgraph.extract.scip import _parse_scip
    assert FIXTURE_SCIP.exists(), "fixture SCIP index missing"
    return _parse_scip(FIXTURE_SCIP, FIXTURE_DIR)


def test_parse_yields_functions_and_classes(scip_result):
    assert scip_result is not None
    labels = {n["label"] for n in scip_result.nodes}
    assert "Function" in labels
    assert "Class" in labels
    names = {n["name"] for n in scip_result.nodes}
    assert "authenticate" in names
    # methods are class-qualified; the class itself is not (no "User.User")
    assert "User.display_name" in names
    assert "User" in names and "User.User" not in names


def test_node_paths_are_absolute(scip_result):
    for n in scip_result.nodes:
        assert Path(n["path"]).is_absolute(), n["path"]
        assert n["path"].endswith(".py")


def test_edges_reference_real_node_uids(scip_result):
    uids = {n["uid"] for n in scip_result.nodes}
    for e in scip_result.edges:
        assert e["source_uid"] in uids
        assert e["target_uid"] in uids


def test_cross_file_call_resolved(scip_result):
    """handlers.get_user calls auth.authenticate — a cross-file CALLS edge."""
    by_uid = {n["uid"]: n for n in scip_result.nodes}
    cross = [
        e for e in scip_result.edges
        if e["relation"] == "CALLS"
        and by_uid[e["source_uid"]]["path"] != by_uid[e["target_uid"]]["path"]
    ]
    assert cross, "expected at least one cross-file CALLS edge"
    # the specific handlers -> auth.authenticate edge
    assert any(
        by_uid[e["source_uid"]]["path"].endswith("handlers.py")
        and by_uid[e["target_uid"]]["name"] == "authenticate"
        for e in cross
    )


def test_calls_are_compiler_extracted(scip_result):
    calls = [e for e in scip_result.edges if e["relation"] == "CALLS"]
    assert calls
    # non-celery calls carry EXTRACTED confidence (compiler-resolved)
    local = [e for e in calls if e.get("call_kind") != "celery_dispatch"]
    assert local and all(e["confidence"] == "EXTRACTED" for e in local)


@pytest.mark.skipif(shutil.which("scip-python") is None, reason="scip-python not installed")
def test_scip_extractor_end_to_end(tmp_path):
    """If scip-python is on PATH, ScipExtractor.run() produces a non-empty graph."""
    from orgraph.extract.scip import ScipExtractor
    target = tmp_path / "proj"
    shutil.copytree(FIXTURE_DIR, target)
    (target / "pyproject.toml").write_text(
        "[tool.pyright]\ninclude = [\".\"]\n\n[project]\nname = \"p\"\nversion = \"0.0.0\"\n",
        encoding="utf-8",
    )
    result = ScipExtractor(repo_path=target, scratch_dir=target / ".scip").run()
    assert result is not None and result.extractor == "scip"
    assert len(result.nodes) > 0
