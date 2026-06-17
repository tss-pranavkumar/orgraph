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
        confidence STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS IMPORTS(
        FROM File TO Module,
        line_number INT64,
        alias STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS INHERITS(
        FROM Class TO Class,
        FROM Class TO Interface,
        FROM Interface TO Interface,
        FROM Struct TO Struct,
        confidence STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS CONTAINS(
        FROM File TO Function,
        FROM File TO Class,
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


def create_schema(db: OrgraphDB) -> None:
    """Create all node and edge tables. Safe to call on an existing DB (IF NOT EXISTS)."""
    for ddl in _NODE_TABLES:
        db.execute(ddl)
    for ddl in _EDGE_TABLES:
        db.execute(ddl)
