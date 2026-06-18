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

TS_FIXTURE_DIR = (Path(__file__).parent / "fixtures" / "simple_typescript").resolve()
TS_FIXTURE_SCIP = Path(__file__).parent / "fixtures" / "simple_typescript.scip"


@pytest.fixture(scope="module")
def scip_result():
    from orgraph.extract.scip import _parse_scip
    assert FIXTURE_SCIP.exists(), "fixture SCIP index missing"
    return _parse_scip(FIXTURE_SCIP, FIXTURE_DIR)


@pytest.fixture(scope="module")
def ts_scip_result():
    from orgraph.extract.scip import _parse_scip
    assert TS_FIXTURE_SCIP.exists(), "TS fixture SCIP index missing"
    return _parse_scip(TS_FIXTURE_SCIP, TS_FIXTURE_DIR)


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


def test_scip_install_hint():
    from orgraph.extract.scip import scip_install_hint
    binary, hint = scip_install_hint("python")
    assert binary == "scip-python"
    assert "npm" in hint  # scip-python is an npm package, not pip
    assert scip_install_hint("cobol") is None


# ── TypeScript: the descriptor decoder + enclosing-range call graph are not
# Python-specific. These run against a committed scip-typescript index fixture
# (tests/fixtures/simple_typescript.scip) to prove the parser generalizes. ──────

def test_ts_parse_yields_functions_and_classes(ts_scip_result):
    assert ts_scip_result is not None
    labels = {n["label"] for n in ts_scip_result.nodes}
    assert "Function" in labels
    assert "Class" in labels
    names = {n["name"] for n in ts_scip_result.nodes}
    assert "authenticate" in names and "verifyToken" in names
    # methods are class-qualified; the class itself is not (no "User.User")
    assert "User.displayName" in names
    assert "User" in names and "User.User" not in names


def test_ts_distinguishes_interface_enum_from_class(ts_scip_result):
    """SCIP descriptors can't tell class/interface/enum apart (all end in '#'),
    so labels are refined from SymbolInformation.documentation. A class must be
    'Class', an interface 'Interface', an enum 'Enum' — never all 'Class'."""
    label_of = {n["name"]: n["label"] for n in ts_scip_result.nodes}
    assert label_of.get("Identifiable") == "Interface", label_of
    assert label_of.get("Role") == "Enum", label_of
    assert label_of.get("User") == "Class"
    assert label_of.get("Order") == "Class"
    # the interface must NOT be mislabeled as a Class
    classes = {n["name"] for n in ts_scip_result.nodes if n["label"] == "Class"}
    assert "Identifiable" not in classes and "Role" not in classes


def test_ts_nodes_are_typescript_and_absolute(ts_scip_result):
    assert ts_scip_result.nodes
    for n in ts_scip_result.nodes:
        assert n["lang"] == "typescript", n
        assert Path(n["path"]).is_absolute() and n["path"].endswith(".ts"), n["path"]


def test_ts_edges_reference_real_node_uids(ts_scip_result):
    uids = {n["uid"] for n in ts_scip_result.nodes}
    for e in ts_scip_result.edges:
        assert e["source_uid"] in uids
        assert e["target_uid"] in uids


def test_ts_cross_file_call_resolved(ts_scip_result):
    """handlers.getUser calls auth.authenticate — a cross-file CALLS edge."""
    by_uid = {n["uid"]: n for n in ts_scip_result.nodes}
    assert any(
        e["relation"] == "CALLS"
        and by_uid[e["source_uid"]]["path"].endswith("handlers.ts")
        and by_uid[e["target_uid"]]["name"] == "authenticate"
        for e in ts_scip_result.edges
    ), "expected handlers.ts -> authenticate cross-file CALLS edge"


def test_ts_calls_are_compiler_extracted(ts_scip_result):
    calls = [e for e in ts_scip_result.edges if e["relation"] == "CALLS"]
    assert calls
    assert all(e["confidence"] == "EXTRACTED" for e in calls)


def test_ts_inherits_resolved(ts_scip_result):
    """`class AdminUser extends User` -> an INHERITS edge (extends -> base)."""
    by_uid = {n["uid"]: n for n in ts_scip_result.nodes}
    assert any(
        e["relation"] == "INHERITS"
        and by_uid[e["source_uid"]]["name"] == "AdminUser"
        and by_uid[e["target_uid"]]["name"] == "User"
        for e in ts_scip_result.edges
    ), "expected AdminUser --|> User INHERITS edge"


def test_scip_install_hint_typescript():
    from orgraph.extract.scip import scip_install_hint
    binary, hint = scip_install_hint("typescript")
    assert binary == "scip-typescript"
    assert "npm" in hint


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


@pytest.mark.skipif(shutil.which("scip-typescript") is None, reason="scip-typescript not installed")
def test_scip_extractor_end_to_end_typescript(tmp_path):
    """If scip-typescript is on PATH, ScipExtractor.run() produces a TS graph."""
    from orgraph.extract.scip import ScipExtractor
    target = tmp_path / "proj"
    shutil.copytree(TS_FIXTURE_DIR, target)
    (target / "package.json").write_text(
        '{ "name": "p", "version": "0.0.0", "private": true }\n', encoding="utf-8",
    )
    (target / "tsconfig.json").write_text(
        '{ "compilerOptions": { "target": "ES2020", "module": "commonjs", '
        '"strict": true, "moduleResolution": "node" }, "include": ["*.ts"] }\n',
        encoding="utf-8",
    )
    result = ScipExtractor(repo_path=target, scratch_dir=target / ".scip").run()
    assert result is not None and result.extractor == "scip"
    assert any(n["lang"] == "typescript" for n in result.nodes)
