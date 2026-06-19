"""Kuzu schema: node tables, edge tables, indexes for orgraph."""
from __future__ import annotations

from orgraph.graph.kuzu import OrgraphDB

# Node tables — Kuzu native DDL
_NODE_TABLES = [
    """CREATE NODE TABLE IF NOT EXISTS Repository(
        path STRING,
        name STRING,
        indexed_at STRING,
        PRIMARY KEY(path)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS File(
        path STRING,
        name STRING,
        relative_path STRING,
        lang STRING,
        is_dependency BOOLEAN,
        PRIMARY KEY(path)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Directory(
        path STRING,
        name STRING,
        PRIMARY KEY(path)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Module(
        uid STRING,
        name STRING,
        lang STRING,
        full_import_name STRING,
        path STRING,
        line_number INT64,
        PRIMARY KEY(uid)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Function(
        uid STRING,
        name STRING,
        path STRING,
        line_number INT64,
        end_line INT64,
        lang STRING,
        source STRING,
        docstring STRING,
        is_dependency BOOLEAN,
        confidence STRING,
        community_id STRING,
        cluster_id STRING,
        http_method STRING,
        http_path STRING,
        PRIMARY KEY(uid)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Class(
        uid STRING,
        name STRING,
        path STRING,
        line_number INT64,
        end_line INT64,
        lang STRING,
        source STRING,
        docstring STRING,
        is_dependency BOOLEAN,
        confidence STRING,
        community_id STRING,
        cluster_id STRING,
        PRIMARY KEY(uid)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Interface(
        uid STRING,
        name STRING,
        path STRING,
        line_number INT64,
        lang STRING,
        is_dependency BOOLEAN,
        confidence STRING,
        PRIMARY KEY(uid)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Struct(
        uid STRING,
        name STRING,
        path STRING,
        line_number INT64,
        lang STRING,
        is_dependency BOOLEAN,
        confidence STRING,
        PRIMARY KEY(uid)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Enum(
        uid STRING,
        name STRING,
        path STRING,
        line_number INT64,
        lang STRING,
        is_dependency BOOLEAN,
        confidence STRING,
        PRIMARY KEY(uid)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Variable(
        uid STRING,
        name STRING,
        path STRING,
        line_number INT64,
        lang STRING,
        is_dependency BOOLEAN,
        confidence STRING,
        PRIMARY KEY(uid)
    )""",
]

# Edge tables — Kuzu native DDL
# Kuzu requires explicit FROM/TO node table pairs
_EDGE_TABLES = [
    """CREATE REL TABLE IF NOT EXISTS CALLS(
        FROM Function TO Function,
        FROM Function TO Class,
        FROM Class TO Function,
        line_number INT64,
        confidence STRING,
        call_kind STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS IMPORTS(
        FROM File TO File,
        line_number INT64,
        alias STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS INHERITS(
        FROM Class TO Class,
        FROM Class TO Interface,
        FROM Interface TO Interface,
        FROM Struct TO Struct,
        line_number INT64,
        confidence STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS CONTAINS(
        FROM File TO Function,
        FROM File TO Class,
        FROM File TO Interface,
        FROM File TO Enum,
        FROM File TO Struct,
        FROM File TO Variable,
        FROM Class TO Function,
        FROM Directory TO File
    )""",
    """CREATE REL TABLE IF NOT EXISTS IMPLEMENTS(
        FROM Class TO Interface,
        FROM Struct TO Interface,
        confidence STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS DEFINED_IN(
        FROM Repository TO File,
        FROM Repository TO Directory
    )""",
]


# Idempotent column additions for graphs built by an older schema version.
# Kuzu's CREATE ... IF NOT EXISTS never alters an existing table, so a DB created
# before a column was introduced keeps the old shape — e.g. a stale CALLS table
# without call_kind silently rejects every `SET r.call_kind = ...` edge write.
_MIGRATIONS = [
    "ALTER TABLE CALLS ADD call_kind STRING DEFAULT 'local'",
    # INHERITS originally shipped with only `confidence`, but _write_edges always
    # sets line_number; without this column every INHERITS write silently fails.
    "ALTER TABLE INHERITS ADD line_number INT64 DEFAULT 0",
]


def _drop_legacy_imports(db: OrgraphDB) -> None:
    """IMPORTS changed from `File→Module` to `File→File`.

    Kuzu cannot ALTER a rel table's FROM/TO endpoints, and `CREATE ... IF NOT
    EXISTS` never redefines an existing table — so a DB built by an older orgraph
    keeps the legacy `File→Module` shape and every new `File→File` write fails.
    The legacy table never persisted a single edge (the unconditional
    `SET r.confidence` write bug guaranteed it), so dropping it loses nothing; the
    create loop then recreates it with the new endpoints. No-op when IMPORTS is
    absent (fresh DB) or already `File→File` (up-to-date DB).
    """
    try:
        rows = db.query_to_dicts("CALL show_connection('IMPORTS') RETURN *")
    except Exception:
        return  # table absent (fresh DB) or unsupported call — nothing to migrate
    blob = " ".join(str(v) for row in rows for v in row.values())
    if "Module" in blob:
        try:
            db.execute("DROP TABLE IMPORTS")
        except Exception:
            pass


def create_schema(db: OrgraphDB) -> None:
    """Create all node and edge tables. Safe to call on an existing DB (IF NOT EXISTS).

    Also applies idempotent column migrations so a graph built by an older orgraph
    version gains columns added later (each ALTER raises if the column already
    exists, which is the expected no-op on an up-to-date DB).

    NOTE: Kuzu rel-table FROM/TO pairs are immutable — neither ALTER nor
    CREATE IF NOT EXISTS can change them. Endpoint changes (e.g. IMPORTS
    File→Module → File→File) require detect-and-drop (see _drop_legacy_imports);
    adding a new FROM/TO pair to a table that holds data requires a full reindex.
    """
    for ddl in _NODE_TABLES:
        db.execute(ddl)
    _drop_legacy_imports(db)
    for ddl in _EDGE_TABLES:
        db.execute(ddl)
    for ddl in _MIGRATIONS:
        try:
            db.execute(ddl)
        except Exception:
            pass  # column already present — table was created at the current version
