"""Tests for Phase 2: topology clustering and Leiden communities."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "simple_python"


def _get_result():
    from orgraph.extract.treesitter import TreeSitterExtractor
    return TreeSitterExtractor(repo_path=FIXTURE).run()


def _get_result_outside_tests(tmp_path: Path):
    """Copy fixture to a non-tests dir so _is_test_file heuristic doesn't exclude all files."""
    target = tmp_path / "simple_python"
    shutil.copytree(FIXTURE, target)
    from orgraph.extract.treesitter import TreeSitterExtractor
    return TreeSitterExtractor(repo_path=target).run(), target


def test_build_repo_context_returns_context():
    from orgraph.topology.context import build_repo_context
    result = _get_result()
    ctx = build_repo_context(result, FIXTURE)
    assert ctx.call_graph is not None
    assert len(ctx.file_summaries) > 0


def test_call_graph_has_edges():
    from orgraph.topology.context import build_repo_context
    result = _get_result()
    ctx = build_repo_context(result, FIXTURE)
    assert len(ctx.call_graph) > 0, "Expected call graph edges from fixture CALLS edges"


def test_call_graph_preserves_celery_call_kind():
    from orgraph.extract.types import ExtractionResult, make_uid
    from orgraph.topology.call_graph import CALL_KIND_CELERY
    from orgraph.topology.context import build_repo_context

    caller_uid = make_uid("initiate_refund_request", "/repo/refund.py", 1)
    task_uid = make_uid("send_mail_task", "/repo/tasks.py", 1)
    result = ExtractionResult(
        nodes=[
            {
                "uid": caller_uid,
                "label": "Function",
                "name": "initiate_refund_request",
                "path": "/repo/refund.py",
                "line_number": 1,
            },
            {
                "uid": task_uid,
                "label": "Function",
                "name": "send_mail_task",
                "path": "/repo/tasks.py",
                "line_number": 1,
            },
        ],
        edges=[
            {
                "source_uid": caller_uid,
                "target_uid": task_uid,
                "relation": "CALLS",
                "line_number": 3,
                "call_kind": CALL_KIND_CELERY,
            }
        ],
    )

    ctx = build_repo_context(result, Path("/repo"))
    edges = ctx.call_graph.get_callees("/repo/refund.py", "initiate_refund_request")
    assert len(edges) == 1
    assert edges[0].call_kind == CALL_KIND_CELERY
    assert edges[0].call_site_line == 3


def test_build_topology_returns_clusters(tmp_path):
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.topology import build_topology_map
    result, target = _get_result_outside_tests(tmp_path)
    ctx = build_repo_context(result, target)
    topology = build_topology_map(ctx)
    assert len(topology.clusters) >= 1


def test_topology_cluster_owns_files(tmp_path):
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.topology import build_topology_map
    result, target = _get_result_outside_tests(tmp_path)
    ctx = build_repo_context(result, target)
    topology = build_topology_map(ctx)
    assert len(topology.clusters) >= 1
    for c in topology.clusters:
        assert len(c.all_files) >= 1, f"Cluster {c.cluster_id} owns no files"


def test_topology_file_cluster_id_covers_all_files(tmp_path):
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.topology import build_topology_map
    result, target = _get_result_outside_tests(tmp_path)
    ctx = build_repo_context(result, target)
    topology = build_topology_map(ctx)
    for f in ctx.file_summaries:
        assert f in topology.file_cluster_id, f"{f} not assigned to any cluster"


def test_leiden_communities_non_empty():
    from orgraph.topology.cluster import build_nx_graph_from_result, cluster
    result = _get_result()
    G = build_nx_graph_from_result(result)
    assert G.number_of_nodes() > 0
    communities = cluster(G)
    assert len(communities) > 0


def test_leiden_communities_cover_all_nodes():
    from orgraph.topology.cluster import build_nx_graph_from_result, cluster
    result = _get_result()
    G = build_nx_graph_from_result(result)
    communities = cluster(G)
    assigned = set()
    for nodes in communities.values():
        assigned.update(nodes)
    assert assigned == set(G.nodes()), "Some nodes not assigned to any community"


def test_topology_json_roundtrip(tmp_path):
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.serialise import load_topology, save_topology
    from orgraph.topology.topology import build_topology_map
    result = _get_result()
    ctx = build_repo_context(result, FIXTURE)
    topology = build_topology_map(ctx)
    save_topology(topology, tmp_path)

    loaded = load_topology(tmp_path)
    assert loaded is not None
    assert len(loaded.clusters) == len(topology.clusters)
    assert loaded.foundational_files == topology.foundational_files


def test_build_index_topology_matches_direct(tmp_path):
    """Topology from the shared build_index pipeline matches a direct build on the same result."""
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.pipeline import build_index
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.topology import build_topology_map

    result, target = _get_result_outside_tests(tmp_path)
    direct = build_topology_map(build_repo_context(result, target))

    orgraph_dir = target / ".orgraph"
    orgraph_dir.mkdir(parents=True, exist_ok=True)
    db = OrgraphDB(orgraph_dir / "graph.kuzu")
    try:
        stats = build_index(db, target, orgraph_dir, rebuild_search=False, result=result)
    finally:
        db.close()

    assert stats["clusters"] == len(direct.clusters)
    assert stats["communities"] >= 1


def test_build_index_warns_on_unextractable_files(tmp_path):
    """A repo whose only code files have no extractor must surface a warning, not silent success."""
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.pipeline import build_index

    target = tmp_path / "sql_only"
    target.mkdir()
    (target / "schema.sql").write_text("CREATE TABLE t (id INT);\n", encoding="utf-8")

    orgraph_dir = target / ".orgraph"
    orgraph_dir.mkdir(parents=True, exist_ok=True)
    db = OrgraphDB(orgraph_dir / "graph.kuzu")
    try:
        stats = build_index(db, target, orgraph_dir, rebuild_search=False)
    finally:
        db.close()

    assert stats["nodes"] == 0
    assert stats["warnings"], "expected a warning when code files yield no symbols"
    assert ".sql" in stats["warnings"][0]


def test_build_index_no_warning_for_supported_repo(tmp_path):
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.pipeline import build_index

    result, target = _get_result_outside_tests(tmp_path)
    orgraph_dir = target / ".orgraph"
    orgraph_dir.mkdir(parents=True, exist_ok=True)
    db = OrgraphDB(orgraph_dir / "graph.kuzu")
    try:
        stats = build_index(db, target, orgraph_dir, rebuild_search=False, result=result)
    finally:
        db.close()

    assert stats["warnings"] == []


def test_communities_json_roundtrip(tmp_path):
    from orgraph.topology.cluster import build_nx_graph_from_result, cluster
    from orgraph.topology.serialise import load_communities, save_communities
    result = _get_result()
    G = build_nx_graph_from_result(result)
    communities = cluster(G)
    save_communities(communities, tmp_path)

    loaded = load_communities(tmp_path)
    assert loaded is not None
    assert set(loaded.keys()) == set(communities.keys())
    for k in communities:
        assert sorted(loaded[k]) == sorted(communities[k])
