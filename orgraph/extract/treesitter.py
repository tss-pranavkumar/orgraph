"""Tree-sitter fallback extractor — wraps graphify's extract().

Used when no SCIP binary is available. Maps graphify's node/edge dict format
to orgraph's unified NodeDict/EdgeDict schema.
"""
from __future__ import annotations

import sys
from pathlib import Path

from orgraph.extract.manifest import _CODE_EXTENSIONS, _IGNORED_DIRS
from orgraph.extract.types import EdgeDict, ExtractionResult, NodeDict, make_uid

# graphify lives in the reference codes; add it to sys.path for import
_GRAPHIFY_ROOT = Path.home() / "tss/codegen/orgraph/.codes/graphify"

_RELATION_MAP = {
    "calls":       "CALLS",
    "imports":     "IMPORTS",
    "imports_from":"IMPORTS",
    "inherits":    "INHERITS",
    "implements":  "IMPLEMENTS",
    "contains":    "CONTAINS",
    "method":      "CONTAINS",
    "references":  "CALLS",   # treat cross-file references as inferred calls
    "embeds":      "INHERITS",
    "mixes_in":    "INHERITS",
    "re_exports":  "IMPORTS",
}

# graphify label → orgraph label
_LABEL_MAP = {
    "class":     "Class",
    "function":  "Function",
    "method":    "Function",
    "interface": "Interface",
    "struct":    "Struct",
    "enum":      "Enum",
    "trait":     "Struct",  # closest match
    "variable":  "Variable",
    "module":    "Module",
    "file":      "File",
}

_IGNORED_FILE_TYPES = frozenset({"rationale", "concept", "paper", "image", "video"})

_FALCON_HTTP: dict[str, str] = {
    "on_get": "GET", "on_post": "POST", "on_put": "PUT",
    "on_patch": "PATCH", "on_delete": "DELETE", "on_options": "OPTIONS",
    "on_head": "HEAD",
}


def _walk_code_files(repo_path: Path) -> list[Path]:
    files: list[Path] = []
    for p in repo_path.rglob("*"):
        if any(part in _IGNORED_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in _CODE_EXTENSIONS:
            files.append(p)
    return files


def _infer_lang(source_file: str) -> str:
    ext = Path(source_file).suffix
    _EXT_LANG = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
        ".scala": "scala", ".rb": "ruby", ".php": "php", ".swift": "swift",
        ".cs": "csharp", ".cpp": "cpp", ".c": "c", ".h": "cpp",
        ".lua": "lua", ".zig": "zig", ".ex": "elixir", ".exs": "elixir",
        ".hs": "haskell", ".dart": "dart", ".sh": "bash", ".sql": "sql",
    }
    return _EXT_LANG.get(ext, "unknown")


class TreeSitterExtractor:
    """Extracts nodes + edges from a repo using graphify's tree-sitter parser."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def run(self) -> ExtractionResult:
        # Ensure graphify is importable
        graphify_src = _GRAPHIFY_ROOT / "graphify"
        if not graphify_src.exists():
            raise RuntimeError(f"graphify not found at {_GRAPHIFY_ROOT}")
        if str(_GRAPHIFY_ROOT) not in sys.path:
            sys.path.insert(0, str(_GRAPHIFY_ROOT))

        from graphify.extract import extract  # type: ignore

        files = _walk_code_files(self.repo_path)
        if not files:
            return ExtractionResult(extractor="treesitter")

        raw = extract(files, cache_root=None, parallel=True)
        return self._convert(raw)

    def _convert(self, raw: dict) -> ExtractionResult:
        raw_nodes: list[dict] = raw.get("nodes", [])
        raw_edges: list[dict] = raw.get("edges", [])

        # Build method-node-id → class name from graphify's "method" edges.
        # Graphify emits a "method" edge from the class node to each method node.
        # This lets us qualify method names as "ClassName.method_name" and detect Falcon handlers.
        id_to_class_name: dict[str, str] = {}
        node_id_to_label: dict[str, str] = {n["id"]: n.get("label", "") for n in raw_nodes}
        for e in raw_edges:
            if e.get("relation") == "method":
                class_label = node_id_to_label.get(e.get("source", ""), "")
                # Class labels in graphify are PascalCase without trailing "()"
                class_name = class_label.rstrip("()").lstrip(".")
                if class_name and not class_name.endswith("()"):
                    id_to_class_name[e.get("target", "")] = class_name

        # Build id → NodeDict map for uid resolution
        id_to_uid: dict[str, str] = {}
        nodes: list[NodeDict] = []

        for n in raw_nodes:
            file_type = n.get("file_type", "code")
            if file_type in _IGNORED_FILE_TYPES:
                continue

            raw_label = n.get("label", "")
            # graphify's 'label' is the display name: "authenticate()", "User", "models.py"
            # Strip trailing "()" to get the clean symbol name for functions.
            name = raw_label.rstrip("()") if raw_label.endswith("()") else raw_label
            name = name.lstrip(".")
            if not name:
                continue

            src_file = n.get("source_file", "")
            # graphify source_file is relative to the common root of passed files.
            # Resolve against repo_path to get the absolute path.
            if src_file:
                candidate = self.repo_path / src_file
                abs_path = str(candidate.resolve()) if candidate.exists() else str(self.repo_path / src_file)
            else:
                abs_path = ""

            src_loc = n.get("source_location", "")
            line_no = int(src_loc.lstrip("L")) if src_loc and src_loc.startswith("L") else 0
            lang = _infer_lang(src_file)

            # Skip bare file nodes (label ends with .py/.js etc.) — we generate File nodes in builder
            if "." in name and name.count(".") == 1 and not name.startswith("."):
                id_to_uid[n["id"]] = make_uid(name, abs_path, 0)
                continue

            # Type inference:
            # graphify labels functions as "name()" and classes as "Name" (PascalCase, no parens)
            is_func = raw_label.endswith("()")
            is_class = (not is_func) and name and name[0].isupper()
            label = "Class" if is_class else "Function"

            # Qualify method names with their parent class to avoid collisions
            # (e.g. two resource classes both having "on_post").
            http_method = ""
            http_path = ""
            class_name = id_to_class_name.get(n["id"])
            if class_name and is_func:
                name = f"{class_name}.{name}"
                bare = name.split(".")[-1]
                if lang == "python" and bare in _FALCON_HTTP:
                    http_method = _FALCON_HTTP[bare]

            uid = make_uid(name, abs_path, line_no)
            id_to_uid[n["id"]] = uid

            node: NodeDict = {
                "uid": uid,
                "label": label,
                "name": name,
                "path": abs_path,
                "line_number": line_no,
                "end_line": line_no,
                "lang": lang,
                "source": "",
                "docstring": "",
                "is_dependency": False,
                "confidence": n.get("confidence", "EXTRACTED"),
                "http_method": http_method,
                "http_path": http_path,
            }
            nodes.append(node)

        edges: list[EdgeDict] = []
        for e in raw_edges:
            src_id = e.get("source", "")
            dst_id = e.get("target", "")
            src_uid = id_to_uid.get(src_id)
            dst_uid = id_to_uid.get(dst_id)
            if not src_uid or not dst_uid or src_uid == dst_uid:
                continue

            relation = _RELATION_MAP.get(e.get("relation", ""), "CALLS")
            confidence = e.get("confidence", "INFERRED")
            line_no = 0
            if src_loc := e.get("source_location", ""):
                if src_loc.startswith("L"):
                    try:
                        line_no = int(src_loc[1:])
                    except ValueError:
                        pass

            edge: EdgeDict = {
                "source_uid": src_uid,
                "target_uid": dst_uid,
                "relation": relation,
                "confidence": confidence,
                "line_number": line_no,
            }
            edges.append(edge)

        return ExtractionResult(nodes=nodes, edges=edges, extractor="treesitter")
