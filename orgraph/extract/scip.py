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

from orgraph.extract.types import EdgeDict, ExtractionResult, NodeDict, make_uid

# Maps extension → (language, binary, install_hint)
_SCIP_MAP: dict[str, tuple[str, str, str]] = {
    ".py":   ("python",     "scip-python",     "npm install -g @sourcegraph/scip-python"),
    ".ipynb":("python",     "scip-python",     "npm install -g @sourcegraph/scip-python"),
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

# Falcon resource method names → HTTP verbs
_FALCON_HTTP: dict[str, str] = {
    "on_get": "GET", "on_post": "POST", "on_put": "PUT",
    "on_patch": "PATCH", "on_delete": "DELETE", "on_options": "OPTIONS",
    "on_head": "HEAD",
}

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


def scip_install_hint(lang: str) -> tuple[str, str] | None:
    """Return (binary, install_hint) for a language's SCIP indexer, or None."""
    for _, (l, binary, hint) in _SCIP_MAP.items():
        if l == lang:
            return binary, hint
    return None


def _binary_for_lang(lang: str) -> str | None:
    for _, (l, binary, _) in _SCIP_MAP.items():
        if l == lang and shutil.which(binary):
            return binary
    return None


def _build_command(lang: str, binary: str, repo_path: Path, out_file: Path, scratch: Path) -> list[str] | None:
    out = str(out_file)
    if binary == "scip-python":
        return [binary, "index", "--cwd", str(repo_path), "--output", out, "--quiet"]
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


# ── SCIP symbol descriptor decoding ───────────────────────────────────────────
# scip-python (and other indexers) leave SymbolInformation.kind=0 and display_name
# empty — the name and kind live in the SCIP *symbol descriptor string*, e.g.
#   "scip-python python . <pkg> module/ClassName#method()."
# We decode that. Techniques adapted from CodeGraphContext's ScipIndexParser.

def _name_from_symbol(symbol: str) -> str:
    """Extract a display name from a SCIP symbol descriptor string."""
    s = re.sub(r"\([0-9a-fA-F]{4,}\)\.?$", "", symbol)   # strip overload hash
    s = re.sub(r"\.\(\$?[^)]*\)", "", s)                  # strip parameter descriptors
    s = s.rstrip(".#")
    s = re.sub(r"\(\)\.?$", "", s)                        # strip call markers ()
    parts = re.split(r"[/#]", s)
    name = parts[-1] if parts else symbol
    name = re.sub(r"^`([^`]+)`$", r"\1", name)            # unwrap `escaped` names
    if " " in name:                                       # space-separated pkg descriptors
        name = name.rsplit(" ", 1)[-1]
    return name


def _label_from_symbol(symbol: str) -> str | None:
    """Map a SCIP symbol descriptor suffix to a *coarse* orgraph node label.

    Returns 'Function' (functions + methods) or 'Type' (any class/interface/enum/
    struct — the descriptor `#` suffix can't tell them apart), or None to skip
    (namespaces, parameters, plain terms/variables). Callers refine 'Type' into
    Class/Interface/Enum/Struct via `_refine_type_label` using the symbol's doc.
    """
    s = symbol.rstrip()
    if s.endswith("/"):            # namespace / module
        return None
    if s.endswith("()."):          # function or method
        return "Function"
    if s.endswith("#"):            # type: class/struct/interface/enum
        return "Type"
    if "#" in s and re.search(r"\([0-9a-fA-F]{4,}\)\.$", s):   # overloaded method
        return "Function"
    return None


# SCIP descriptors use the same `#` suffix for every type (class, interface, enum,
# struct, type alias), so the descriptor alone can't distinguish them. Both
# scip-python and scip-typescript DO record the real keyword in
# SymbolInformation.documentation, e.g. "```ts\ninterface Foo\n```" — decode that.
_TYPE_KW_RE = re.compile(
    r"```[a-z]*\s*(?:export\s+|declare\s+|default\s+|abstract\s+|const\s+)*"
    r"(class|interface|enum|struct|trait|type|namespace)\b"
)
_TYPE_KW_LABEL: dict[str, str | None] = {
    "class": "Class",
    "interface": "Interface",
    "trait": "Interface",
    "enum": "Enum",
    "struct": "Struct",
    "type": None,        # bare type aliases aren't structural nodes — skip
    "namespace": None,   # namespaces aren't symbols we model
}


def _refine_type_label(documentation) -> str | None:
    """Resolve a `#`-type symbol to Class/Interface/Enum/Struct from its hover doc.

    Returns the precise label, None to skip (type alias / namespace), or 'Class'
    as a safe fallback when no keyword is present (descriptor is a type, kind
    unknown — matches the old coarse behaviour rather than dropping the node).
    """
    for text in documentation:
        m = _TYPE_KW_RE.search(text)
        if m:
            return _TYPE_KW_LABEL.get(m.group(1), "Class")
    return "Class"


def _is_definition(occ) -> bool:
    role = getattr(occ, "symbol_roles", getattr(occ, "role", 0))
    return bool(role & 1)


def _find_enclosing_symbol(ref_line: int, def_occs: list) -> str | None:
    """Innermost definition whose enclosing_range contains ref_line (1-based)."""
    best: str | None = None
    best_start = -1
    for occ in def_occs:
        er = list(getattr(occ, "enclosing_range", []))
        if not er:
            continue
        enc_start, enc_end = (er[0] + 1, er[2] + 1) if len(er) == 4 else (er[0] + 1, er[0] + 1)
        if enc_start <= ref_line <= enc_end and enc_start > best_start:
            best, best_start = occ.symbol, enc_start
    return best


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()
    except Exception:
        return []


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

    # Reuse the tree-sitter heuristics for Falcon routes + celery dispatch so that
    # find_entry_points works identically under SCIP (SCIP itself knows neither).
    from orgraph.extract.treesitter import TreeSitterExtractor
    ts = TreeSitterExtractor(repo_path=repo_path)
    routes = ts._collect_falcon_routes()

    # INHERITS relationships still come from SymbolInformation (kind/display_name
    # are empty there, but is_implementation relationships are populated).
    # SymbolInformation.documentation carries the real type keyword, so it's also
    # where we learn class-vs-interface-vs-enum (the descriptor can't say).
    bases: dict[str, list[str]] = {}
    type_label: dict[str, str | None] = {}   # scip symbol → Class/Interface/Enum/Struct (or None)
    for doc in index.documents:
        for si in doc.symbols:
            b = [r.symbol for r in si.relationships if getattr(r, "is_implementation", False)]
            if b:
                bases[si.symbol] = b
            if si.symbol.endswith("#"):
                type_label[si.symbol] = _refine_type_label(si.documentation)

    nodes: list[NodeDict] = []
    uid_map: dict[str, str] = {}     # scip symbol → uid
    label_of: dict[str, str] = {}    # scip symbol → label

    # ── Pass 1: definitions → nodes ────────────────────────────────────────────
    for doc in index.documents:
        abs_path = str(repo_path / doc.relative_path)
        lang_info = _SCIP_MAP.get(Path(doc.relative_path).suffix)
        lang = lang_info[0] if lang_info else "unknown"
        src = _read_lines(repo_path / doc.relative_path)

        for occ in doc.occurrences:
            sym = occ.symbol
            if sym.startswith("local ") or not _is_definition(occ):
                continue
            label = _label_from_symbol(sym)
            if label is None:
                continue
            if label == "Type":
                # refine class/interface/enum/struct from the symbol's hover doc;
                # None means a type alias / namespace we don't model as a node.
                label = type_label.get(sym, "Class")
                if label is None:
                    continue
            line_no = (occ.range[0] + 1) if occ.range else 0
            name = _name_from_symbol(sym)

            # Qualify methods as ClassName.method (matches tree-sitter naming) and
            # tag Falcon HTTP handlers.
            http_method = http_path = ""
            class_name, _, member = sym.split("/")[-1].partition("#")
            if member and class_name:  # a member of the class, not the class itself
                name = f"{class_name}.{name}"
                if name.rsplit(".", 1)[-1] in _FALCON_HTTP:
                    http_method = _FALCON_HTTP[name.rsplit(".", 1)[-1]]
                    http_path = routes.get(class_name, "")

            uid = make_uid(name, abs_path, line_no)
            uid_map[sym] = uid
            label_of[sym] = label
            nodes.append({
                "uid": uid, "label": label, "name": name, "path": abs_path,
                "line_number": line_no, "end_line": line_no, "lang": lang,
                "source": src[line_no - 1].strip() if 0 < line_no <= len(src) else "",
                "docstring": "", "is_dependency": False, "confidence": "EXTRACTED",
                "http_method": http_method, "http_path": http_path,
            })

    # ── Pass 2: references → CALLS edges (caller via enclosing_range) ───────────
    edges: list[EdgeDict] = []
    seen: set[tuple[str, str, int]] = set()
    for doc in index.documents:
        def_occs = [
            o for o in doc.occurrences
            if _is_definition(o) and not o.symbol.startswith("local ")
            and list(getattr(o, "enclosing_range", []))
        ]
        src = _read_lines(repo_path / doc.relative_path)
        for occ in doc.occurrences:
            sym = occ.symbol
            if sym.startswith("local ") or _is_definition(occ):
                continue
            dst_uid = uid_map.get(sym)
            if not dst_uid or label_of.get(sym) != "Function":
                continue  # only edges to known functions/methods are calls
            r = list(occ.range)
            if not r:
                continue
            row = r[0]
            col_end = r[3] if len(r) >= 4 else (r[2] if len(r) >= 3 else (r[1] if len(r) > 1 else 0))
            if row >= len(src) or not src[row][col_end:].lstrip().startswith("("):
                continue  # reference not followed by '(' → not a call site
            ref_line = row + 1
            caller_sym = _find_enclosing_symbol(ref_line, def_occs)
            src_uid = uid_map.get(caller_sym) if caller_sym else None
            if not src_uid or src_uid == dst_uid:
                continue
            key = (src_uid, dst_uid, ref_line)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source_uid": src_uid, "target_uid": dst_uid, "relation": "CALLS",
                "confidence": "EXTRACTED", "line_number": ref_line, "call_kind": "local",
            })

    # ── Pass 3: INHERITS ───────────────────────────────────────────────────────
    for sym, base_syms in bases.items():
        src_uid = uid_map.get(sym)
        if not src_uid:
            continue
        for b in base_syms:
            dst_uid = uid_map.get(b)
            if dst_uid and dst_uid != src_uid:
                edges.append({
                    "source_uid": src_uid, "target_uid": dst_uid,
                    "relation": "INHERITS", "confidence": "EXTRACTED", "line_number": 0,
                })

    # Celery dispatch parity (reuse tree-sitter heuristic over the SCIP nodes).
    edges.extend(ts._extract_celery_dispatch_edges(nodes))

    return ExtractionResult(nodes=nodes, edges=edges, extractor="scip")
