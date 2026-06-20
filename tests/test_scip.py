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

GO_FIXTURE_DIR = (Path(__file__).parent / "fixtures" / "simple_go").resolve()
GO_FIXTURE_SCIP = Path(__file__).parent / "fixtures" / "simple_go.scip"

MONOREPO_FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "simple_typescript_monorepo"
).resolve()
MONOREPO_A_SCIP = MONOREPO_FIXTURE_DIR / "a.scip"
MONOREPO_B_SCIP = MONOREPO_FIXTURE_DIR / "b.scip"


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


@pytest.fixture(scope="module")
def go_scip_result():
    from orgraph.extract.scip import _parse_scip
    assert GO_FIXTURE_SCIP.exists(), "Go fixture SCIP index missing"
    return _parse_scip(GO_FIXTURE_SCIP, GO_FIXTURE_DIR)


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
        if e.get("relation") == "IMPORTS":
            continue  # IMPORTS is File→File, keyed by src_path/dst_path, not uid
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
        if e.get("relation") == "IMPORTS":
            continue  # IMPORTS is File→File, keyed by src_path/dst_path, not uid
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


def test_scip_emits_file_to_file_imports(scip_result):
    """SCIP path now produces File->File IMPORTS edges (import-line heuristic)."""
    imports = [e for e in scip_result.edges if e.get("relation") == "IMPORTS"]
    assert imports, "expected IMPORTS edges from the SCIP fixture"
    pairs = {(Path(e["src_path"]).name, Path(e["dst_path"]).name) for e in imports}
    assert ("handlers.py", "auth.py") in pairs, f"missing known import; got {pairs}"
    assert ("handlers.py", "models.py") in pairs, f"missing known import; got {pairs}"
    for e in imports:
        assert e["src_path"] != e["dst_path"], "self-import should be excluded"
        assert e.get("source_uid") == "" and e.get("target_uid") == "", "IMPORTS is path-keyed"


def test_ts_arrow_function_exports_become_function_nodes(ts_scip_result):
    """`export const fn = (...) => ...` exports must become Function nodes,
    while plain `export const VERSION = "1.0.0"` must not. The discriminator is
    the `=>` in SymbolInformation.documentation (scip.py:_ARROW_FN_DOC_RE)."""
    by_name = {n["name"]: n for n in ts_scip_result.nodes}
    assert by_name.get("formatName", {}).get("label") == "Function", \
        "arrow-fn `formatName` should be a Function node"
    assert by_name.get("sum", {}).get("label") == "Function", \
        "arrow-fn `sum` should be a Function node"
    assert by_name.get("logName", {}).get("label") == "Function", \
        "regular `function logName` must still resolve"
    # Plain non-function const must NOT have become a node.
    assert "VERSION" not in by_name, \
        f"plain const `VERSION` must NOT be labeled as a Function — {by_name.get('VERSION')!r}"


# ── Go: chi/gin/stdlib HTTP route detection ─────────────────────────────────

def test_go_parse_yields_function_nodes(go_scip_result):
    assert go_scip_result is not None
    names = {n["name"] for n in go_scip_result.nodes}
    # Top-level functions
    for expected in ("getUser", "createItem", "healthz", "replaceItem", "formatGreeting"):
        assert expected in names, f"missing Go function {expected}: {sorted(names)}"


def test_go_entry_points_detected(go_scip_result):
    """chi `r.Get(...)`, gin `r.POST(...)`, chi generic `r.Method(verb, ...)`,
    and stdlib `http.HandleFunc(...)` should each tag their handler node."""
    by_name = {n["name"]: n for n in go_scip_result.nodes}
    assert by_name["getUser"]["http_method"] == "GET"
    assert by_name["getUser"]["http_path"] == "/users/{id}"
    assert by_name["createItem"]["http_method"] == "POST"
    assert by_name["createItem"]["http_path"] == "/items"
    assert by_name["replaceItem"]["http_method"] == "PUT"
    assert by_name["replaceItem"]["http_path"] == "/items/{id}"
    assert by_name["healthz"]["http_method"] == "ANY"
    assert by_name["healthz"]["http_path"] == "/health"
    # Non-route function must be untagged.
    assert by_name["formatGreeting"]["http_method"] == ""
    assert by_name["formatGreeting"]["http_path"] == ""


def test_cross_package_imports_resolve():
    """In a monorepo, `import { greet } from "../b/src/bar"` in package a must
    emit a File→File IMPORTS edge to package b's bar.ts — the gap that
    `_run_per_package`'s global sym_def_path closes."""
    from orgraph.extract.scip import _collect_global_sym_def_paths, _parse_scip

    jobs = [
        (MONOREPO_A_SCIP, MONOREPO_FIXTURE_DIR / "packages" / "a"),
        (MONOREPO_B_SCIP, MONOREPO_FIXTURE_DIR / "packages" / "b"),
    ]
    sym_def_global = _collect_global_sym_def_paths(jobs)
    # b's `greet` definition must appear in the global map.
    assert any(
        sym.endswith("/greet().") and "b 0.0.0" in sym
        for sym in sym_def_global
    ), f"greet missing from global sym_def_path: {list(sym_def_global)[:5]}"

    # parse package a with the global map — should emit the cross-package edge.
    result_a = _parse_scip(
        MONOREPO_A_SCIP,
        MONOREPO_FIXTURE_DIR,
        doc_root=MONOREPO_FIXTURE_DIR / "packages" / "a",
        sym_def_path_global=sym_def_global,
    )
    assert result_a is not None
    imports = [e for e in result_a.edges if e["relation"] == "IMPORTS"]
    assert imports, "expected at least one IMPORTS edge from package a"

    bar_path = str(MONOREPO_FIXTURE_DIR / "packages" / "b" / "src" / "bar.ts")
    foo_path = str(MONOREPO_FIXTURE_DIR / "packages" / "a" / "src" / "foo.ts")
    cross = [e for e in imports if e["src_path"] == foo_path and e["dst_path"] == bar_path]
    assert cross, (
        f"expected packages/a/src/foo.ts -> packages/b/src/bar.ts IMPORTS edge; "
        f"got {[(e['src_path'], e['dst_path']) for e in imports]}"
    )


def test_cross_package_imports_drop_without_global_map():
    """Regression: the same parse WITHOUT the global map drops the cross-package
    edge. Proves the global map is what closes the gap, not some other code path."""
    from orgraph.extract.scip import _parse_scip

    result_a = _parse_scip(
        MONOREPO_A_SCIP,
        MONOREPO_FIXTURE_DIR,
        doc_root=MONOREPO_FIXTURE_DIR / "packages" / "a",
    )
    assert result_a is not None
    bar_path = str(MONOREPO_FIXTURE_DIR / "packages" / "b" / "src" / "bar.ts")
    foo_path = str(MONOREPO_FIXTURE_DIR / "packages" / "a" / "src" / "foo.ts")
    cross = [
        e for e in result_a.edges
        if e["relation"] == "IMPORTS"
        and e["src_path"] == foo_path
        and e["dst_path"] == bar_path
    ]
    assert not cross, (
        "without global sym_def_path, cross-package import should drop — "
        f"got {cross}"
    )


def test_go_route_collector_ignores_commented_examples(tmp_path):
    """A commented `// r.Get("/example", h)` must not register a route."""
    from orgraph.extract.treesitter import TreeSitterExtractor
    repo = tmp_path / "p"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package p\n"
        "// r.Get(\"/example\", commented)  // documentation example\n"
        "func live(w, r interface{}) {}\n"
        "func init() { router.Get(\"/live\", live) }\n",
        encoding="utf-8",
    )
    routes = TreeSitterExtractor(repo).__class__._collect_go_http_routes(
        TreeSitterExtractor(repo)
    )
    assert "live" in routes and routes["live"] == ("GET", "/live")
    assert "commented" not in routes, f"comment leaked into routes: {routes}"


def test_scip_imports_persist_end_to_end(scip_result, tmp_path):
    """SCIP IMPORTS edges survive ingest into Kuzu as File->File."""
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    b = GraphBuilder(db=db, repo_path=FIXTURE_DIR)
    b.clear()
    b.ingest(scip_result)
    n = db.query_to_dicts("MATCH (:File)-[:IMPORTS]->(:File) RETURN count(*) AS c")[0]["c"]
    assert n > 0, "expected persisted File->File IMPORTS from SCIP extraction"
    db.close()
