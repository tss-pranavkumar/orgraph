"""File-hash manifest for incremental indexing."""
from __future__ import annotations

import hashlib
from pathlib import Path

import orjson

_IGNORED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".orgraph", ".mypy_cache", ".pytest_cache",
    "coverage", ".tox",
})

_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".scala", ".groovy",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".lua", ".zig",
    ".ex", ".exs", ".hs", ".dart", ".sh", ".bash",
    ".tf", ".hcl", ".sql",
})


def _md5(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def _walk_repo(repo_path: Path) -> list[Path]:
    files: list[Path] = []
    for p in repo_path.rglob("*"):
        if any(part in _IGNORED_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in _CODE_EXTENSIONS:
            files.append(p)
    return files


class Manifest:
    """Tracks file → md5 hash for a repo. Enables incremental re-indexing."""

    def __init__(self, orgraph_dir: Path) -> None:
        self._path = orgraph_dir / "manifest.json"
        self._hashes: dict[str, str] = {}

    def load(self) -> None:
        if self._path.exists():
            self._hashes = orjson.loads(self._path.read_bytes())

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(orjson.dumps(self._hashes))

    def changed_files(self, repo_path: Path) -> list[Path]:
        """Return files added or modified since last save."""
        changed: list[Path] = []
        for p in _walk_repo(repo_path):
            key = str(p)
            current = _md5(p)
            if self._hashes.get(key) != current:
                changed.append(p)
        return changed

    def all_files(self, repo_path: Path) -> list[Path]:
        return _walk_repo(repo_path)

    def update(self, files: list[Path]) -> None:
        for p in files:
            self._hashes[str(p)] = _md5(p)

    def is_empty(self) -> bool:
        return len(self._hashes) == 0
