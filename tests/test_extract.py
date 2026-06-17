"""Tests for extraction layer."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "simple_python"


def test_treesitter_returns_nodes():
    from orgraph.extract.treesitter import TreeSitterExtractor
    result = TreeSitterExtractor(FIXTURE).run()
    assert result.extractor == "treesitter"
    assert result.node_count() > 0, "Expected at least some nodes from the fixture"


def test_treesitter_finds_classes():
    from orgraph.extract.treesitter import TreeSitterExtractor
    result = TreeSitterExtractor(FIXTURE).run()
    classes = [n for n in result.nodes if n["label"] == "Class"]
    assert len(classes) >= 2, f"Expected User and Order classes, got: {[c['name'] for c in classes]}"


def test_treesitter_finds_functions():
    from orgraph.extract.treesitter import TreeSitterExtractor
    result = TreeSitterExtractor(FIXTURE).run()
    fns = [n for n in result.nodes if n["label"] == "Function"]
    assert len(fns) >= 3, f"Expected at least 3 functions, got {len(fns)}"


def test_treesitter_has_edges():
    from orgraph.extract.treesitter import TreeSitterExtractor
    result = TreeSitterExtractor(FIXTURE).run()
    assert result.edge_count() > 0, "Expected call/import edges"


def test_treesitter_marks_celery_dispatch_edges(tmp_path):
    repo = tmp_path / "celery_project"
    repo.mkdir()
    (repo / "tasks.py").write_text(
        "def send_mail_task(user_id):\n"
        "    return user_id\n",
        encoding="utf-8",
    )
    (repo / "refund.py").write_text(
        "from tasks import send_mail_task\n\n"
        "def initiate_refund_request(user_id):\n"
        "    send_mail_task.apply_async(args=[user_id])\n"
        "    send_mail_task.delay(user_id)\n",
        encoding="utf-8",
    )

    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.topology.call_graph import CALL_KIND_CELERY

    result = TreeSitterExtractor(repo).run()
    uid_to_name = {n["uid"]: n["name"] for n in result.nodes}
    celery_edges = [
        e for e in result.edges
        if e.get("relation") == "CALLS" and e.get("call_kind") == CALL_KIND_CELERY
    ]
    assert len(celery_edges) == 2
    assert {uid_to_name[e["target_uid"]] for e in celery_edges} == {"send_mail_task"}


def test_treesitter_extracts_falcon_add_route_paths(tmp_path):
    repo = tmp_path / "falcon_project"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from handlers import RefundResource\n\n"
        "def create_app(app):\n"
        "    app.add_route('/refund', RefundResource())\n",
        encoding="utf-8",
    )
    (repo / "handlers.py").write_text(
        "class RefundResource:\n"
        "    def on_post(self, req, resp):\n"
        "        return None\n",
        encoding="utf-8",
    )

    from orgraph.extract.treesitter import TreeSitterExtractor

    result = TreeSitterExtractor(repo).run()
    handlers = [n for n in result.nodes if n["name"] == "RefundResource.on_post"]
    assert handlers
    assert handlers[0]["http_method"] == "POST"
    assert handlers[0]["http_path"] == "/refund"


def test_scip_skips_gracefully_when_unavailable():
    """SCIP extractor should return None when no binary is installed, not raise."""
    import shutil
    from orgraph.extract.scip import _detect_primary_lang, _binary_for_lang
    lang = _detect_primary_lang(FIXTURE)
    if lang:
        binary = _binary_for_lang(lang)
        if binary is None:
            # Binary not installed — ScipExtractor.run() should return None
            from orgraph.extract.scip import ScipExtractor
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                result = ScipExtractor(FIXTURE, Path(tmp)).run()
            assert result is None


def test_manifest_tracks_changes(tmp_path):
    from orgraph.extract.manifest import Manifest
    m = Manifest(tmp_path)
    # Initially empty, all files are "changed"
    changed = m.changed_files(FIXTURE)
    assert len(changed) > 0
    # After updating, nothing changed
    m.update(changed)
    m.save()
    m2 = Manifest(tmp_path)
    m2.load()
    changed2 = m2.changed_files(FIXTURE)
    assert len(changed2) == 0


def test_uid_is_deterministic():
    from orgraph.extract.types import make_uid
    uid1 = make_uid("login", "/app/auth.py", 10)
    uid2 = make_uid("login", "/app/auth.py", 10)
    uid3 = make_uid("logout", "/app/auth.py", 10)
    assert uid1 == uid2
    assert uid1 != uid3
