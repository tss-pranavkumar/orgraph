"""Tests for Kuzu schema and graph builder."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "simple_python"


def test_schema_creates_without_error(tmp_path):
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)  # should not raise
    db.close()


def test_schema_is_idempotent(tmp_path):
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    create_schema(db)  # second call with IF NOT EXISTS — should not raise
    db.close()


def test_builder_ingest_returns_counts(tmp_path):
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = TreeSitterExtractor(FIXTURE).run()
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    builder = GraphBuilder(db=db, repo_path=FIXTURE)
    nodes, edges = builder.ingest(result)
    assert nodes > 0, "Expected nodes written"
    db.close()


def test_builder_can_query_functions(tmp_path):
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = TreeSitterExtractor(FIXTURE).run()
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    GraphBuilder(db=db, repo_path=FIXTURE).ingest(result)

    rows = db.query_to_dicts("MATCH (f:Function) RETURN f.name AS name")
    names = [r["name"] for r in rows]
    assert len(names) > 0, "Expected queryable Function nodes"
    db.close()


def test_builder_can_query_files(tmp_path):
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = TreeSitterExtractor(FIXTURE).run()
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    GraphBuilder(db=db, repo_path=FIXTURE).ingest(result)

    rows = db.query_to_dicts("MATCH (f:File) RETURN f.name AS name")
    names = [r["name"] for r in rows]
    assert any("auth" in n or "handler" in n or "model" in n for n in names), \
        f"Expected fixture files, got: {names}"
    db.close()


# ── Regression tests: nodes/edges must actually *persist*, not just count. ──────
# The builder swallows write errors (except Exception: pass), so a schema/param
# mismatch silently drops data while ingest() still reports success. These assert
# what reaches the graph. Input is the committed TS SCIP fixture (no binary needed),
# which carries Class + CALLS + INHERITS — the shapes the older code dropped.

TS_FIXTURE_DIR = (Path(__file__).parent / "fixtures" / "simple_typescript").resolve()
TS_FIXTURE_SCIP = Path(__file__).parent / "fixtures" / "simple_typescript.scip"


def _ingest_ts_fixture(tmp_path):
    from orgraph.extract.scip import _parse_scip
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = _parse_scip(TS_FIXTURE_SCIP, TS_FIXTURE_DIR)
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    GraphBuilder(db=db, repo_path=TS_FIXTURE_DIR).ingest(result)
    return db, result


def test_builder_persists_class_nodes(tmp_path):
    """Regression: _node_params always passes http_method, but the Class MERGE
    doesn't reference it — Kuzu rejects unused params, so every Class (and
    Interface/Struct/Enum/Variable) node write was silently dropped."""
    db, _ = _ingest_ts_fixture(tmp_path)
    classes = {r["name"] for r in db.query_to_dicts("MATCH (c:Class) RETURN c.name AS name")}
    assert {"User", "Order", "AdminUser"} <= classes, classes
    db.close()


def test_builder_persists_interface_and_enum_nodes(tmp_path):
    """Interface/Enum nodes (refined from SCIP docs) must persist with their real
    label — not collapse into Class, and not get dropped at ingest."""
    db, _ = _ingest_ts_fixture(tmp_path)
    ifaces = {r["name"] for r in db.query_to_dicts("MATCH (i:Interface) RETURN i.name AS name")}
    enums = {r["name"] for r in db.query_to_dicts("MATCH (e:Enum) RETURN e.name AS name")}
    assert "Identifiable" in ifaces, ifaces
    assert "Role" in enums, enums
    db.close()


def test_builder_persists_calls_and_inherits(tmp_path):
    """Regression: INHERITS shipped without line_number while _write_edges always
    SETs it -> every INHERITS edge dropped; CALLS likewise needs call_kind."""
    db, _ = _ingest_ts_fixture(tmp_path)
    calls = db.query_to_dicts("MATCH ()-[x:CALLS]->() RETURN count(x) AS c")[0]["c"]
    assert calls > 0, "no CALLS edges persisted"
    inh = db.query_to_dicts(
        "MATCH (s:Class)-[:INHERITS]->(d:Class) RETURN s.name AS s, d.name AS d"
    )
    assert any(r["s"] == "AdminUser" and r["d"] == "User" for r in inh), inh
    db.close()


def test_builder_contains_links_every_symbol(tmp_path):
    """Regression: _write_file_nodes skipped all but the first symbol per file,
    so CONTAINS linked one symbol per file instead of all of them."""
    db, result = _ingest_ts_fixture(tmp_path)
    contains = db.query_to_dicts("MATCH (:File)-[x:CONTAINS]->() RETURN count(x) AS c")[0]["c"]
    contained = ("Function", "Class", "Interface", "Enum", "Struct", "Variable")
    symbols = sum(1 for n in result.nodes if n["label"] in contained)
    assert contains == symbols, f"{contains} CONTAINS edges for {symbols} symbols"
    db.close()


def test_schema_migrates_legacy_db(tmp_path):
    """Regression: a graph built by an older schema (CALLS without call_kind,
    INHERITS without line_number) must gain those columns via create_schema so
    that edge writes setting them no longer fail."""
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    db = OrgraphDB(tmp_path / "legacy.kuzu")
    # Simulate the legacy schema (pre-call_kind / pre-line_number).
    db.execute("CREATE NODE TABLE Function(uid STRING, PRIMARY KEY(uid))")
    db.execute("CREATE NODE TABLE Class(uid STRING, PRIMARY KEY(uid))")
    db.execute("CREATE REL TABLE CALLS(FROM Function TO Function, line_number INT64, confidence STRING)")
    db.execute("CREATE REL TABLE INHERITS(FROM Class TO Class, confidence STRING)")

    create_schema(db)  # must ALTER in the missing columns

    # Writes that SET the migrated columns must now succeed.
    db.execute("MERGE (:Function {uid: 'a'})")
    db.execute("MERGE (:Function {uid: 'b'})")
    db.execute(
        "MATCH (s:Function {uid:'a'}), (d:Function {uid:'b'}) "
        "MERGE (s)-[r:CALLS]->(d) SET r.call_kind = 'local'"
    )
    db.execute("MERGE (:Class {uid: 'x'})")
    db.execute("MERGE (:Class {uid: 'y'})")
    db.execute(
        "MATCH (s:Class {uid:'x'}), (d:Class {uid:'y'}) "
        "MERGE (s)-[r:INHERITS]->(d) SET r.line_number = 1"
    )
    assert db.query_to_dicts("MATCH ()-[x:CALLS]->() RETURN count(x) AS c")[0]["c"] == 1
    assert db.query_to_dicts("MATCH ()-[x:INHERITS]->() RETURN count(x) AS c")[0]["c"] == 1
    db.close()


# ── Phase B: data-loss regression guards ──────────────────────────────────────

def _ingest_fixture(tmp_path):
    """Ingest the simple_python fixture and return (db, builder)."""
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema

    result = TreeSitterExtractor(FIXTURE).run()
    db = OrgraphDB(tmp_path / "graph.kuzu")
    create_schema(db)
    builder = GraphBuilder(db=db, repo_path=FIXTURE)
    builder.clear()
    builder.ingest(result)
    return db, builder


def test_imports_persist_file_to_file(tmp_path):
    """IMPORTS edges survive the write (regression: confidence-column crash + bad endpoints)."""
    db, _ = _ingest_fixture(tmp_path)
    rows = db.query_to_dicts("MATCH (s:File)-[:IMPORTS]->(d:File) RETURN s.name AS s, d.name AS d")
    assert rows, "Expected at least one File->File IMPORTS edge"
    pairs = {(r["s"], r["d"]) for r in rows}
    assert ("handlers.py", "auth.py") in pairs, f"missing known import; got {pairs}"
    for r in rows:
        assert r["s"] != r["d"], "self-import should be dropped"
    db.close()


def test_deps_returns_imports(tmp_path):
    """get_dependencies('imports') returns rows now that IMPORTS persists."""
    from orgraph.graph.query import get_dependencies
    db, _ = _ingest_fixture(tmp_path)
    handlers = db.query_to_dicts("MATCH (f:File) WHERE f.name = 'handlers.py' RETURN f.path AS p")[0]["p"]
    deps = get_dependencies(db, handlers, "imports", 1)
    names = {d["name"] for d in deps}
    assert {"auth.py", "models.py"} <= names, f"expected imported files, got {names}"
    db.close()


def test_no_whole_relation_silent_drop(tmp_path):
    """Every extracted relation persists at least one edge; no schema-error drops."""
    _, builder = _ingest_fixture(tmp_path)
    s = builder.last_ingest_summary
    for rel, n in s["extracted"].items():
        if n > 0:
            assert s["persisted"].get(rel, 0) > 0, f"relation {rel} fully dropped: {s}"
    schema_errors = {k: v for k, v in s["drops"].items() if k.endswith(":schema-error")}
    assert not schema_errors, f"schema-error drops indicate a column/table bug: {schema_errors}"


def test_calls_persist_without_schema_error(tmp_path):
    """CALLS must never drop to a schema error, and the bulk must persist.

    (A small number of edges legitimately drop as external/unresolved — calls to
    stdlib/3rd-party symbols, or graphify duplicate-name artifacts — so we assert
    the bulk persists rather than an exact count.)
    """
    _, builder = _ingest_fixture(tmp_path)
    s = builder.last_ingest_summary
    assert s["drops"].get("CALLS:schema-error", 0) == 0, f"CALLS hit a schema error: {s['drops']}"
    extracted = s["extracted"].get("CALLS", 0)
    persisted = s["persisted"].get("CALLS", 0)
    assert persisted >= extracted - 2, f"too many CALLS lost: {persisted}/{extracted} ({s['drops']})"


def test_imports_relation_has_no_confidence_column():
    """White-box guard for the original IMPORTS crash: never SET a column it lacks."""
    from orgraph.graph.builder import _EDGE_COLUMNS
    assert "confidence" not in _EDGE_COLUMNS["IMPORTS"]
