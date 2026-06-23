"""SCIP-based extraction — compiler-accurate call graph.

Runs the appropriate scip-<lang> CLI binary on a repo, parses the resulting
index.scip protobuf, and returns an ExtractionResult with NodeDicts/EdgeDicts.

Falls back gracefully (returns None) when no SCIP binary is installed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path


def _abs_doc_path(base: Path, rel: str) -> str:
    """Canonicalise a SCIP document path string.

    scip-typescript invoked in `packages/a` with a tsconfig that pulls in
    `../b/src/bar.ts` emits a `relative_path` containing `..`. Joining with
    base gives `packages/a/../b/src/bar.ts` — the same file as
    `packages/b/src/bar.ts` but different as a graph key. We collapse `..`
    segments (pure-string, no filesystem syscalls) so cross-scip lookups and
    per-scip definitions agree on the same canonical key.
    """
    return os.path.normpath(str(base / rel))

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

# Directories where a nested tsconfig.json indicates a sub-package in a TS/JS
# monorepo (pnpm/yarn/npm workspaces, Turborepo, Nx). scip-typescript invoked at
# the workspace root with a project-references aggregator tsconfig produces zero
# documents — we have to run it per-package.
_TS_PKG_PARENTS = frozenset({"packages", "apps", "services", "libs", "modules"})

# scip-python / scip-typescript do NOT set the SCIP Import symbol-role on import
# occurrences (verified: only Definition + Read/plain-reference roles appear), so
# imports are detected by the source line a reference sits on. Covers Python
# (`import x` / `from x import y`), TS/JS (`import ... from '...'`,
# `export ... from '...'`, `require(...)`), and Go's `import ( ... )` block
# whose inner lines look like `\t"net/http"` or `\tpkg "github.com/foo/bar"`.
_IMPORT_LINE_RE = re.compile(
    r"^\s*(from\s|import\s)"
    r"|\bfrom\s+['\"]"
    r"|require\s*\("
    # Go import-block inner line: optional alias + quoted path + EOL anchor so we
    # don't match incidental quoted strings appearing mid-expression elsewhere.
    r"|^\s*(?:[A-Za-z_][\w]*\s+|\.\s+|_\s+)?[\"`][^\"`]+[\"`]\s*$"
)


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


def _workspace_pkg_sources(pkgs: list[Path]) -> dict[str, Path]:
    """Map workspace package name → its source-entry .ts file (absolute path).

    Reads each sub-package's `package.json` for its `name`, then probes the
    common source layouts in order: `src/index.ts`, `src/index.tsx`,
    `index.ts`, `index.tsx`. Packages that don't expose a source-side entry
    (e.g. shipped only as built `dist/`) are skipped — there's nothing to
    point a `paths` alias at.
    """
    out: dict[str, Path] = {}
    for pkg_dir in pkgs:
        pj = pkg_dir / "package.json"
        if not pj.exists():
            continue
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = data.get("name")
        if not name:
            continue
        for rel in ("src/index.ts", "src/index.tsx", "index.ts", "index.tsx"):
            candidate = pkg_dir / rel
            if candidate.is_file():
                out[name] = candidate
                break
    return out


@contextmanager
def _augmented_tsconfig(pkg_dir: Path, workspace_paths: dict[str, Path]):
    """Temporarily inject cross-package `paths` into `pkg_dir/tsconfig.json`.

    scip-typescript only resolves a workspace import to its source if the
    importing package's tsconfig has a `paths` alias pointing at it
    (`node_modules/@scope/pkg` typically resolves to the package's built
    `dist/`, which may not exist on a fresh checkout). We synthesize those
    aliases on the fly: backup → write augmented → run → restore. Guarded
    with try/finally so we never leave the user's tsconfig mutated.
    """
    tsconfig = pkg_dir / "tsconfig.json"
    backup: str | None = None
    if tsconfig.exists() and workspace_paths:
        try:
            original = tsconfig.read_text(encoding="utf-8")
            data = json.loads(original)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            co = data.setdefault("compilerOptions", {})
            if not isinstance(co, dict):
                raise ValueError("compilerOptions is not an object")
            co.setdefault("baseUrl", ".")
            existing_paths = co.get("paths") if isinstance(co.get("paths"), dict) else {}
            self_name = data.get("name") if isinstance(data.get("name"), str) else None
            merged = dict(existing_paths)  # don't clobber existing
            for name, src_path in workspace_paths.items():
                if name == self_name or name in merged:
                    continue
                try:
                    rel = os.path.relpath(src_path, start=pkg_dir)
                except Exception:
                    rel = str(src_path)
                merged[name] = [rel]
            if merged != existing_paths:
                co["paths"] = merged
                backup = original
                tsconfig.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            backup = None  # leave original alone — best-effort
    try:
        yield
    finally:
        if backup is not None:
            try:
                tsconfig.write_text(backup, encoding="utf-8")
            except Exception:
                pass  # surface as a stderr warning would be nicer; ignore for now


def _find_ts_packages(repo_path: Path) -> list[Path]:
    """Find sub-package roots in a TS/JS monorepo (each has its own tsconfig.json).

    Looks for `tsconfig.json` under conventional workspace parents (packages/, apps/,
    services/, libs/, modules/). Returns absolute directories, deduped, never
    including the repo root itself. Empty list = not a monorepo, index at root.
    """
    pkgs: list[Path] = []
    seen: set[Path] = set()
    for parent in _TS_PKG_PARENTS:
        parent_dir = repo_path / parent
        if not parent_dir.is_dir():
            continue
        for tc in parent_dir.rglob("tsconfig.json"):
            if any(part in _IGNORED_DIRS for part in tc.parts):
                continue
            pkg_dir = tc.parent.resolve()
            if pkg_dir in seen or pkg_dir == repo_path.resolve():
                continue
            seen.add(pkg_dir)
            pkgs.append(pkg_dir)
    return pkgs


def _find_go_modules(repo_path: Path) -> list[Path]:
    """Find every Go module root in a `go.work` workspace / multi-module repo.

    Every directory containing a `go.mod` is its own module; unlike TS workspace
    layouts, Go modules can live anywhere (`grpc/example1/go.mod`,
    `services/auth/go.mod`, etc.), so the search is repo-wide. Returns absolute
    directories deduped on resolved path. The repo root is *included* when it
    has its own `go.mod` AND there's at least one sub-module — `_run_per_package`
    needs every module covered, including the root.
    """
    mods: list[Path] = []
    seen: set[Path] = set()
    root = repo_path.resolve()
    has_sub = False
    for gm in repo_path.rglob("go.mod"):
        if any(part in _IGNORED_DIRS for part in gm.parts):
            continue
        mod_dir = gm.parent.resolve()
        if mod_dir != root:
            has_sub = True
        if mod_dir in seen:
            continue
        seen.add(mod_dir)
        mods.append(mod_dir)
    if not has_sub:
        return []
    return mods


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

        # Monorepo / multi-module path: scip-typescript at a workspace root
        # indexes nothing useful, and scip-go at the root of a `go.work` repo
        # only sees the root module. Run per-package and merge.
        if binary == "scip-typescript":
            pkgs = _find_ts_packages(self.repo_path)
            if pkgs:
                merged = self._run_per_package(pkgs, binary, lang)
                if merged and merged.nodes:
                    return merged
                # fall through to root run as a last resort
        elif binary == "scip-go":
            mods = _find_go_modules(self.repo_path)
            if mods:
                merged = self._run_per_package(mods, binary, lang)
                if merged and merged.nodes:
                    return merged

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

        result = _parse_scip(out_file, self.repo_path)

        # Detect silent failure: scip-<lang> exits 0 and writes a valid index
        # header but emits zero documents (Pyright environment-resolution failure
        # is the main cause for scip-python). An empty result is worse than
        # tree-sitter — fall back rather than silently returning an empty graph.
        if not result or not result.nodes:
            import sys
            print(
                f"orgraph: {binary} produced 0 nodes for {self.repo_path.name} "
                "(index has no documents — likely an environment-resolution failure). "
                "Falling back to tree-sitter.",
                file=sys.stderr,
            )
            return None

        return result

    def _run_per_package(self, pkgs: list[Path], binary: str, lang: str) -> ExtractionResult | None:
        """Run scip-<lang> inside each sub-package/module, parse and merge.

        Two passes over each sub-package's scip:
          1. Run scip-<lang> inside the package, then a CHEAP pre-sweep of
             definition occurrences to populate a global `sym_def_path` map
             (scip symbol → abs file path) — this is what makes cross-package
             IMPORTS edges resolve.
          2. Full `_parse_scip` with that global map plumbed in, so Pass 4 can
             find the defining file of a symbol that was imported from another
             sub-package / module.

        TS-specific path: `_augmented_tsconfig` temporarily injects cross-package
        `paths` aliases. Go doesn't need it (go.mod-based resolution is enough).
        """
        merged_nodes: list[NodeDict] = []
        merged_edges: list[EdgeDict] = []
        seen_uids: set[str] = set()
        ok = 0

        # TS-only: synthesize cross-package `paths` so scip-typescript resolves
        # workspace imports to package sources (vs missing/unbuilt `dist/`).
        workspace_paths = (
            _workspace_pkg_sources(pkgs) if binary == "scip-typescript" else {}
        )

        # ── Pass A: run scip in each sub-package, collect definition paths ────
        scip_jobs: list[tuple[Path, Path]] = []  # (scip_file, doc_root=pkg_dir)
        for i, pkg_dir in enumerate(pkgs):
            out_file = self.scratch_dir / f"index-{i}.scip"
            cmd = _build_command(lang, binary, pkg_dir, out_file, self.scratch_dir)
            if not cmd:
                continue
            try:
                with _augmented_tsconfig(pkg_dir, workspace_paths):
                    proc = subprocess.run(
                        cmd, cwd=str(pkg_dir),
                        capture_output=True, text=True, timeout=300,
                    )
                if proc.returncode != 0 or not out_file.exists():
                    continue
            except Exception:
                continue
            scip_jobs.append((out_file, pkg_dir))

        sym_def_path_global = _collect_global_sym_def_paths(scip_jobs)

        # ── Pass B: full parse each scip with the global map plumbed in ───────
        for scip_file, pkg_dir in scip_jobs:
            result = _parse_scip(
                scip_file, self.repo_path,
                doc_root=pkg_dir,
                sym_def_path_global=sym_def_path_global,
            )
            if not result or not result.nodes:
                continue
            ok += 1
            for node in result.nodes:
                if node["uid"] in seen_uids:
                    continue
                seen_uids.add(node["uid"])
                merged_nodes.append(node)
            merged_edges.extend(result.edges)

        if not merged_nodes:
            return None

        edge_seen: set[tuple] = set()
        deduped: list[EdgeDict] = []
        for e in merged_edges:
            key = (
                e.get("source_uid"), e.get("target_uid"), e.get("relation"),
                e.get("line_number"), e.get("src_path"), e.get("dst_path"),
            )
            if key in edge_seen:
                continue
            edge_seen.add(key)
            deduped.append(e)

        import sys
        print(
            f"orgraph: scip-typescript indexed {ok}/{len(pkgs)} sub-package(s) "
            f"→ {len(merged_nodes)} nodes, {len(deduped)} edges",
            file=sys.stderr,
        )
        return ExtractionResult(nodes=merged_nodes, edges=deduped, extractor="scip")


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


def _label_from_symbol_with_doc(symbol: str, arrow_fn_syms: set[str]) -> str | None:
    """Same as `_label_from_symbol`, but recognises arrow-function exports.

    `arrow_fn_syms` is the precomputed set of `.`-suffix symbols whose hover doc
    contains a `=>` arrow — the discriminator that separates `const fn = (x) => x`
    from a plain `const PI = 3.14`.
    """
    base_label = _label_from_symbol(symbol)
    if base_label is not None:
        return base_label
    if symbol.endswith(".") and symbol in arrow_fn_syms:
        return "Function"
    return None


# Arrow-function exports (`export const fn = () => {}`) come through SCIP with a
# `.` (term) descriptor — same suffix as plain `const PI = 3.14`. We can't label
# every `.`-suffixed symbol Function without polluting the graph, so we look at
# SymbolInformation.documentation for the `=>` of an arrow-fn signature.
# Negative lookbehinds avoid matching `==>`, `<=>`, `!=>`, `>=>`.
_ARROW_FN_DOC_RE = re.compile(r"(?<![=!<>])=>")


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


# scip-go emits a different version segment for cross-module references vs the
# definition: an import in module a sees `example.com/b . `example.com/b`/`
# (placeholder `.`) while module b's own definition is
# `example.com/b a6a7a5c3a1b7 `example.com/b`/` (resolved hash). Same symbol,
# different string. We normalize to the version-less form so the global
# sym_def_path map matches across modules. scip-typescript also includes a
# version (e.g. `npm @tinyhttp/router 2.2.5 ...`); same normalisation works.
_SCIP_VERSION_SEG_RE = re.compile(
    r"^(scip-(?:go|typescript|python)\s+\S+\s+\S+\s+)(\S+)(\s)"
)


def _scip_unversioned(sym: str) -> str:
    """Replace the version segment of a SCIP symbol with `.` for cross-scip match."""
    return _SCIP_VERSION_SEG_RE.sub(r"\1.\3", sym, count=1)


def _collect_global_sym_def_paths(
    scip_jobs: list[tuple[Path, Path]],
) -> dict[str, str]:
    """Pre-sweep: open every per-package scip and record `symbol → abs def path`.

    Used by `_run_per_package` to build a cross-scip definition map BEFORE any
    per-package parse runs Pass 4. Without this, every IMPORTS edge that crosses
    a workspace boundary drops (the importing scip doesn't know where the
    imported symbol is defined — that fact lives in the other package's scip).

    Each `scip_jobs` entry is `(scip_file, doc_root)`; `doc_root` is the
    directory scip-<lang> ran in, so the document's relative_path is joined to
    it to recover the absolute file path of the definition.
    """
    try:
        from orgraph.extract import scip_pb2  # type: ignore
    except Exception:
        return {}
    out: dict[str, str] = {}
    for scip_file, doc_root in scip_jobs:
        try:
            idx = scip_pb2.Index()
            idx.ParseFromString(scip_file.read_bytes())
        except Exception:
            continue
        for doc in idx.documents:
            abs_path = _abs_doc_path(Path(doc_root), doc.relative_path)
            for occ in doc.occurrences:
                sym = occ.symbol
                if sym.startswith("local ") or not _is_definition(occ):
                    continue
                out.setdefault(sym, abs_path)
                # Also store the unversioned form so cross-module references —
                # which scip-go emits with version=`.` — resolve.
                u = _scip_unversioned(sym)
                if u != sym:
                    out.setdefault(u, abs_path)
    return out


def _parse_scip(
    index_path: Path,
    repo_path: Path,
    doc_root: Path | None = None,
    sym_def_path_global: dict[str, str] | None = None,
) -> ExtractionResult | None:
    """Parse a SCIP index.

    `doc_root` is the directory scip-<lang> ran in (its document `relative_path`
    fields are relative to this). Defaults to repo_path when the binary was run
    at the repo root.

    `sym_def_path_global` is an optional cross-scip definition map (scip symbol →
    abs file path) used by Pass 4 to resolve IMPORTS edges that cross
    sub-package boundaries — without it, monorepo workspaces drop every
    cross-package import (each per-package scip only knows its own definitions).
    `_run_per_package` builds it via a pre-sweep and passes it here.
    """
    try:
        from orgraph.extract import scip_pb2  # type: ignore
    except Exception:
        return None

    try:
        index = scip_pb2.Index()
        index.ParseFromString(index_path.read_bytes())
    except Exception:
        return None

    base = Path(doc_root) if doc_root else repo_path

    # Reuse the tree-sitter heuristics for Falcon routes + celery dispatch so that
    # find_entry_points works identically under SCIP (SCIP itself knows neither).
    from orgraph.extract.treesitter import TreeSitterExtractor
    ts = TreeSitterExtractor(repo_path=repo_path)
    routes = ts._collect_falcon_routes()
    fastify_routes = ts._collect_fastify_routes()
    go_routes = ts._collect_go_http_routes()
    py_routes = ts._collect_python_http_routes()

    # INHERITS relationships still come from SymbolInformation (kind/display_name
    # are empty there, but is_implementation relationships are populated).
    # SymbolInformation.documentation carries the real type keyword, so it's also
    # where we learn class-vs-interface-vs-enum (the descriptor can't say).
    bases: dict[str, list[str]] = {}
    type_label: dict[str, str | None] = {}   # scip symbol → Class/Interface/Enum/Struct (or None)
    arrow_fn_syms: set[str] = set()          # `.`-suffix symbols whose hover doc shows `=>`
    for doc in index.documents:
        for si in doc.symbols:
            b = [r.symbol for r in si.relationships if getattr(r, "is_implementation", False)]
            if b:
                bases[si.symbol] = b
            if si.symbol.endswith("#"):
                type_label[si.symbol] = _refine_type_label(si.documentation)
            elif si.symbol.endswith(".") and any(_ARROW_FN_DOC_RE.search(t) for t in si.documentation):
                arrow_fn_syms.add(si.symbol)

    nodes: list[NodeDict] = []
    uid_map: dict[str, str] = {}     # scip symbol → uid
    label_of: dict[str, str] = {}    # scip symbol → label
    sym_def_path: dict[str, str] = {}   # scip symbol → defining file abs path (any defined symbol)
    # (short_name, abs_path) → uid for Function nodes only — used by star-import fallback in Pass 2
    name_path_index: dict[tuple[str, str], str] = {}
    # (method_uid, class_scip_sym) pairs — flushed to CONTAINS edges after Pass 1
    method_class_pairs: list[tuple[str, str]] = []

    # ── Pass 1: definitions → nodes ────────────────────────────────────────────
    for doc in index.documents:
        abs_path = _abs_doc_path(base, doc.relative_path)
        lang_info = _SCIP_MAP.get(Path(doc.relative_path).suffix)
        lang = lang_info[0] if lang_info else "unknown"
        src = _read_lines(base / doc.relative_path)

        for occ in doc.occurrences:
            sym = occ.symbol
            if sym.startswith("local ") or not _is_definition(occ):
                continue
            sym_def_path.setdefault(sym, abs_path)   # record before the node-label filter
            label = _label_from_symbol_with_doc(sym, arrow_fn_syms)
            if label is None:
                continue
            if label == "Type":
                # refine class/interface/enum/struct from the symbol's hover doc;
                # None means a type alias / namespace we don't model as a node.
                label = type_label.get(sym, "Class")
                if label is None:
                    continue
            line_no = (occ.range[0] + 1) if occ.range else 0

            # Use enclosing_range to get the real function/class body end line.
            er = list(getattr(occ, "enclosing_range", []))
            end_line = (er[2] + 1) if len(er) >= 3 else line_no

            name = _name_from_symbol(sym)

            # Qualify methods as ClassName.method (matches tree-sitter naming) and
            # tag Falcon/Fastify HTTP handlers.
            http_method = http_path = ""
            class_name, _, member = sym.split("/")[-1].partition("#")
            if member and class_name:  # a member of the class, not the class itself
                name = f"{class_name}.{name}"
                if name.rsplit(".", 1)[-1] in _FALCON_HTTP:
                    http_method = _FALCON_HTTP[name.rsplit(".", 1)[-1]]
                    http_path = routes.get(class_name, "")
                # Derive the class SCIP symbol so we can emit a CONTAINS edge.
                # Method sym ends with "ClassName#method()."; class sym ends with "ClassName#".
                hash_pos = sym.rfind("#")
                if hash_pos != -1:
                    method_class_pairs.append((make_uid(name, abs_path, line_no), sym[:hash_pos + 1]))
            elif label == "Function" and lang in ("typescript", "javascript"):
                if name in fastify_routes:
                    http_method, http_path = fastify_routes[name]
            elif label == "Function" and lang == "go":
                bare = name.split(".")[-1]
                if bare in go_routes:
                    http_method, http_path = go_routes[bare]
            elif label == "Function" and lang == "python":
                bare = name.split(".")[-1]
                if bare in py_routes:
                    http_method, http_path = py_routes[bare]

            uid = make_uid(name, abs_path, line_no)
            uid_map[sym] = uid
            label_of[sym] = label
            if label == "Function":
                short_name = name.rsplit(".", 1)[-1]  # strip ClassName. prefix for methods
                name_path_index[(short_name, abs_path)] = uid
            nodes.append({
                "uid": uid, "label": label, "name": name, "path": abs_path,
                "line_number": line_no, "end_line": end_line, "lang": lang,
                "source": src[line_no - 1].strip() if 0 < line_no <= len(src) else "",
                "docstring": "", "is_dependency": False, "confidence": "EXTRACTED",
                "http_method": http_method, "http_path": http_path,
            })

    # ── Pass 1b: Class→Function CONTAINS edges ─────────────────────────────────
    edges: list[EdgeDict] = []
    for method_uid, class_sym in method_class_pairs:
        class_uid = uid_map.get(class_sym)
        if class_uid and class_uid != method_uid:
            edges.append({
                "source_uid": class_uid, "target_uid": method_uid,
                "relation": "CONTAINS", "confidence": "EXTRACTED", "line_number": 0,
            })

    # ── Pass 2: references → CALLS edges (caller via enclosing_range) ───────────
    seen: set[tuple[str, str, int]] = set()
    for doc in index.documents:
        def_occs = [
            o for o in doc.occurrences
            if _is_definition(o) and not o.symbol.startswith("local ")
            and list(getattr(o, "enclosing_range", []))
        ]
        src = _read_lines(base / doc.relative_path)
        for occ in doc.occurrences:
            sym = occ.symbol
            if sym.startswith("local ") or _is_definition(occ):
                continue
            dst_uid = uid_map.get(sym)
            if dst_uid is None:
                # Star-import fallback: SCIP resolves `fetchOne` (imported via
                # `from module import *`) to the module namespace symbol (ends with
                # `/`). Extract the call token from source and look it up by
                # (name, file) in name_path_index (Function nodes only).
                if sym.endswith("/"):
                    def_path = sym_def_path.get(sym)
                    if def_path:
                        r_tmp = list(occ.range)
                        if r_tmp and r_tmp[0] < len(src):
                            col_s = r_tmp[1] if len(r_tmp) > 1 else 0
                            col_e = r_tmp[3] if len(r_tmp) >= 4 else (r_tmp[2] if len(r_tmp) >= 3 else col_s)
                            token = src[r_tmp[0]][col_s:col_e].strip()
                            dst_uid = name_path_index.get((token, def_path))
                if not dst_uid:
                    continue
            elif label_of.get(sym) != "Function":
                continue  # only edges to known functions/methods are calls
            r = list(occ.range)
            if not r:
                continue
            row = r[0]
            col_end = r[3] if len(r) >= 4 else (r[2] if len(r) >= 3 else (r[1] if len(r) > 1 else 0))
            if row >= len(src):
                continue
            line = src[row]
            col_start = r[1] if len(r) > 1 else 0
            if "#" in line[:col_start]:
                continue  # reference sits after a '#' on its line → commented-out code
            if not line[col_end:].lstrip().startswith("("):
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

    # ── Pass 4: imports → File→File edges (import-line heuristic) ──────────────
    # A reference occurrence sitting on an import line whose symbol is defined in
    # another file is a file-level dependency. Deduped to one edge per file pair —
    # the same File→File model the tree-sitter path and `deps` use.
    import_seen: set[tuple[str, str]] = set()
    for doc in index.documents:
        abs_path = _abs_doc_path(base, doc.relative_path)
        src = _read_lines(base / doc.relative_path)
        if not src:
            continue
        for occ in doc.occurrences:
            sym = occ.symbol
            if sym.startswith("local ") or _is_definition(occ):
                continue
            r = list(occ.range)
            if not r:
                continue
            row = r[0]
            if row >= len(src) or not _IMPORT_LINE_RE.search(src[row]):
                continue
            # Prefer this scip's own definition (single-package fast path); fall
            # back to the cross-scip global map when we're inside a monorepo run.
            # The unversioned-form lookup catches scip-go's cross-module pattern
            # where ref version is `.` but def has the resolved module hash.
            dst_path = sym_def_path.get(sym)
            if not dst_path and sym_def_path_global is not None:
                dst_path = sym_def_path_global.get(sym)
                if not dst_path:
                    dst_path = sym_def_path_global.get(_scip_unversioned(sym))
            if not dst_path or dst_path == abs_path:
                continue
            key = (abs_path, dst_path)
            if key in import_seen:
                continue
            import_seen.add(key)
            edges.append({
                "source_uid": "", "target_uid": "", "relation": "IMPORTS",
                "confidence": "EXTRACTED", "line_number": row + 1,
                "src_path": abs_path, "dst_path": dst_path, "alias": "",
            })

    # Celery dispatch parity (reuse tree-sitter heuristic over the SCIP nodes).
    edges.extend(ts._extract_celery_dispatch_edges(nodes))

    return ExtractionResult(nodes=nodes, edges=edges, extractor="scip")
