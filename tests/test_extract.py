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
