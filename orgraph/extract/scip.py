"""SCIP-based extraction — compiler-accurate call graph.

Runs the appropriate scip-<lang> CLI binary on a repo, parses the resulting
index.scip protobuf, and returns an ExtractionResult with NodeDicts/EdgeDicts.

Falls back gracefully (returns None) when no SCIP binary is installed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from orgraph.extract.types import EdgeDict, ExtractionResult, NodeDict, make_uid

# Maps extension → (language, binary, install_hint)
_SCIP_MAP: dict[str, tuple[str, str, str]] = {
    ".py":   ("python",     "scip-python",     "pip install scip-python"),
    ".ipynb":("python",     "scip-python",     "pip install scip-python"),
    ".ts":   ("typescript", "scip-typescript", "npm install -g @sourcegraph/scip-typescript"),
    ".tsx":  ("typescript", "scip-typescript", "npm install -g @sourcegraph/scip-typescript"),
    ".js":   ("javascript", "scip-typescript", "npm install -g @sourcegraph/scip-typescript"),
    ".jsx":  ("javascript", "scip-typescript", "npm install -g @sourcegraph/scip-typescript"),
    ".mjs":  ("javascript", "scip-typescript", "npm install -g @sourcegraph/scip-typescript"),
    ".go":   ("go",         "scip-go",         "go install github.com/sourcegraph/scip-go/...@latest"),
    ".rs":   ("rust",       "scip-rust",       "cargo install scip-rust"),
    ".java": ("java",       "scip-java",       "see https://github.com/sourcegraph/scip-java"),
    ".kt":   ("kotlin",     "scip-java",       "see https://github.com/sourcegraph/scip-java"),
    ".cs":   ("csharp",     "scip-dotnet",     "dotnet tool install --global scip-dotnet"),
    ".rb":   ("ruby",       "scip-ruby",       "gem install scip-ruby"),
    ".php":  ("php",        "scip-php",        "composer global require davidrjenni/scip-php"),
}

# SCIP SymbolKind values we care about
_KIND_FUNCTION = {17, 26}   # Function, Method

# Falcon resource method names → HTTP verbs
_FALCON_HTTP: dict[str, str] = {
    "on_get": "GET", "on_post": "POST", "on_put": "PUT",
    "on_patch": "PATCH", "on_delete": "DELETE", "on_options": "OPTIONS",
    "on_head": "HEAD",
}
_KIND_CLASS    = {7}         # Class
_KIND_INTERFACE= {20, 54}   # Interface, Protocol
_KIND_STRUCT   = {49}
_KIND_TRAIT    = {53}
_KIND_ENUM     = {18}
_KIND_VARIABLE = {15, 61}   # Variable, Property

_IGNORED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"})


def _detect_primary_lang(repo_path: Path) -> str | None:
    counts: dict[str, int] = {}
    for p in repo_path.rglob("*"):
        if any(part in _IGNORED_DIRS for part in p.parts):
            continue
        lang_info = _SCIP_MAP.get(p.suffix)
        if lang_info:
            lang = lang_info[0]
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def _binary_for_lang(lang: str) -> str | None:
    for _, (l, binary, _) in _SCIP_MAP.items():
        if l == lang and shutil.which(binary):
            return binary
    return None


def _build_command(lang: str, binary: str, repo_path: Path, out_file: Path, scratch: Path) -> list[str] | None:
    out = str(out_file)
    if binary == "scip-python":
        return [binary, "index", "--project-root", str(repo_path), "--output", out]
    if binary == "scip-typescript":
        return [binary, "index", "--output", out]
    if binary == "scip-go":
        return [binary, "--output", out]
    if binary == "scip-rust":
        return [binary, "index", "--output", out]
    if binary == "scip-java":
        return [binary, "index", "--output", out]
    if binary == "scip-dotnet":
        return [binary, "index", "--output", out]
    if binary == "scip-ruby":
        return [binary, "index", "--output", out]
    if binary == "scip-php":
        return [binary, "index", "--output", out]
    return None


class ScipExtractor:
    """Runs SCIP on a repo and returns an ExtractionResult. Returns None if unavailable."""

    def __init__(self, repo_path: Path, scratch_dir: Path) -> None:
        self.repo_path = repo_path
        self.scratch_dir = scratch_dir

    def run(self) -> ExtractionResult | None:
        lang = _detect_primary_lang(self.repo_path)
        if not lang:
            return None
        binary = _binary_for_lang(lang)
        if not binary:
            return None

        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        out_file = self.scratch_dir / "index.scip"

        cmd = _build_command(lang, binary, self.repo_path, out_file, self.scratch_dir)
        if not cmd:
            return None

        try:
            proc = subprocess.run(
                cmd, cwd=str(self.repo_path),
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0 or not out_file.exists():
                return None
        except Exception:
            return None

        return _parse_scip(out_file, self.repo_path)


def _parse_scip(index_path: Path, repo_path: Path) -> ExtractionResult | None:
    try:
        from orgraph.extract import scip_pb2  # type: ignore
    except Exception:
        return None

    try:
        index = scip_pb2.Index()
        index.ParseFromString(index_path.read_bytes())
    except Exception:
        return None

    # Pass 1 — build symbol definition table
    sym_table: dict[str, dict[str, Any]] = {}
    for doc in index.documents:
        for occ in doc.occurrences:
            if occ.symbol.startswith("local "):
                continue
            role = getattr(occ, "symbol_roles", getattr(occ, "role", 0))
            if role & 1:  # definition bit
                sym_table[occ.symbol] = {
                    "file": doc.relative_path,
                    "line": (occ.range[0] + 1) if occ.range else 0,
                }

    for doc in index.documents:
        for sym_info in doc.symbols:
            if sym_info.symbol in sym_table:
                sym_table[sym_info.symbol].update({
                    "display_name": sym_info.display_name,
                    "documentation": "\n".join(sym_info.documentation),
                    "kind": sym_info.kind,
                    "bases": [r.symbol for r in sym_info.relationships if r.is_implementation],
                })

    # Pass 2 — build nodes
    nodes: list[NodeDict] = []
    uid_map: dict[str, str] = {}  # scip_symbol → uid

    for sym, info in sym_table.items():
        kind = info.get("kind", 0)
        name = info.get("display_name", "") or sym.split(".")[-1].rstrip("()")
        rel_path = info.get("file", "")
        abs_path = str(repo_path / rel_path) if rel_path else ""
        line_no = info.get("line", 0)

        if kind in _KIND_FUNCTION:
            label = "Function"
        elif kind in _KIND_CLASS:
            label = "Class"
        elif kind in _KIND_INTERFACE:
            label = "Interface"
        elif kind in _KIND_STRUCT:
            label = "Struct"
        elif kind in _KIND_ENUM:
            label = "Enum"
        elif kind in _KIND_VARIABLE:
            label = "Variable"
        else:
            continue  # skip unknown kinds

        # Detect language from file extension
        ext = Path(rel_path).suffix if rel_path else ""
        lang_info = _SCIP_MAP.get(ext)
        lang = lang_info[0] if lang_info else "unknown"

        # For Python methods (kind=26), extract class name from SCIP symbol
        # SCIP symbol format: "scip-python . path/to/file.py/ClassName#method_name()."
        # Use class-qualified name to avoid collisions across different resource classes.
        http_method = ""
        http_path = ""
        if kind == 26 and lang == "python":  # Method kind
            last_segment = sym.split("/")[-1] if "/" in sym else sym
            if "#" in last_segment:
                class_name = last_segment.split("#")[0]
                if class_name:
                    name = f"{class_name}.{name}"
                    if name.split(".")[-1] in _FALCON_HTTP:
                        http_method = _FALCON_HTTP[name.split(".")[-1]]

        uid = make_uid(name, abs_path, line_no)
        uid_map[sym] = uid

        node: NodeDict = {
            "uid": uid,
            "label": label,
            "name": name,
            "path": abs_path,
            "line_number": line_no,
            "end_line": line_no,
            "lang": lang,
            "source": "",
            "docstring": info.get("documentation", "") or "",
            "is_dependency": False,
            "confidence": "EXTRACTED",
            "http_method": http_method,
            "http_path": http_path,
        }

        # Try to read source snippet
        src_path = repo_path / rel_path if rel_path else None
        if src_path and src_path.exists():
            try:
                lines = src_path.read_text(errors="replace").splitlines()
                if 0 < line_no <= len(lines):
                    node["source"] = lines[line_no - 1].strip()
            except Exception:
                pass

        nodes.append(node)

    # Pass 3 — build CALLS + INHERITS edges
    edges: list[EdgeDict] = []

    for doc in index.documents:
        rel_path = doc.relative_path
        abs_path = str(repo_path / rel_path)
        src_lines: list[str] = []
        try:
            src_lines = (repo_path / rel_path).read_text(errors="replace").splitlines()
        except Exception:
            pass

        # CALLS: reference occurrences where the position follows a call-like token
        caller_sym: str | None = None
        for occ in doc.occurrences:
            if occ.symbol.startswith("local "):
                continue
            role = getattr(occ, "symbol_roles", getattr(occ, "role", 0))
            if role & 1:
                caller_sym = occ.symbol
            else:
                # Reference — check if it looks like a call (next non-space char is '(')
                if occ.range and len(occ.range) >= 4 and src_lines:
                    row, col_end = occ.range[0], occ.range[3] if len(occ.range) > 3 else occ.range[2]
                    if row < len(src_lines):
                        remainder = src_lines[row][col_end:]
                        if remainder.lstrip().startswith("("):
                            callee_sym = occ.symbol
                            src_uid = uid_map.get(caller_sym) if caller_sym else None
                            dst_uid = uid_map.get(callee_sym)
                            if src_uid and dst_uid and src_uid != dst_uid:
                                edges.append({
                                    "source_uid": src_uid,
                                    "target_uid": dst_uid,
                                    "relation": "CALLS",
                                    "confidence": "EXTRACTED",
                                    "line_number": occ.range[0] + 1,
                                })

    # INHERITS edges from bases
    for sym, info in sym_table.items():
        src_uid = uid_map.get(sym)
        if not src_uid:
            continue
        for base_sym in info.get("bases", []):
            dst_uid = uid_map.get(base_sym)
            if dst_uid and src_uid != dst_uid:
                edges.append({
                    "source_uid": src_uid,
                    "target_uid": dst_uid,
                    "relation": "INHERITS",
                    "confidence": "EXTRACTED",
                    "line_number": 0,
                })

    return ExtractionResult(nodes=nodes, edges=edges, extractor="scip")
