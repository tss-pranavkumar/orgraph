"""Tree-sitter fallback extractor — wraps graphify's extract().

Used when no SCIP binary is available. Maps graphify's node/edge dict format
to orgraph's unified NodeDict/EdgeDict schema.
"""
from __future__ import annotations

import re
from pathlib import Path

from orgraph.extract.manifest import _CODE_EXTENSIONS, _IGNORED_DIRS
from orgraph.extract.types import EdgeDict, ExtractionResult, NodeDict, make_uid
from orgraph.topology.call_graph import CALL_KIND_CELERY

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

_FALCON_ROUTE_RE = re.compile(
    r"\.add_route\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*"
    r"(?P<class>[A-Za-z_][\w.]*)\s*\("
)
# Matches: app.add_route(settings.API_PREFIX + '/path', MyResource())
_FALCON_ROUTE_CONCAT_RE = re.compile(
    r"\.add_route\(\s*\w[\w.]*\s*\+\s*['\"](?P<path>/[^'\"]*)['\"]"
    r"\s*,\s*(?P<class>[A-Za-z_][\w.]*)\s*\("
)
_CELERY_DISPATCH_RE = re.compile(
    r"\b(?P<target>[A-Za-z_][\w.]*)\s*\.\s*(?P<method>apply_async|delay)\s*\("
)

# Fastify / Express route registrations:
#   plugin.get('/path', handler)
#   plugin.post('/path', { preHandler: [...] }, controller.handler)
#   app.get('/path', handler)  router.post('/path', handler)  server.delete('/path', h)
_FASTIFY_ROUTE_RE = re.compile(
    r"\b(?:plugin|fastify|app|server|router)\."
    r"(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*"
    r"['\"`](?P<path>[^'\"`]+)['\"`]\s*"
    r"(?:,\s*\{[^}]*\})?"   # optional options/schema object
    r"\s*,\s*(?P<handler>[A-Za-z_][\w.]*)",
    re.IGNORECASE,
)
_FASTIFY_HTTP: dict[str, str] = {
    "get": "GET", "post": "POST", "put": "PUT",
    "patch": "PATCH", "delete": "DELETE", "head": "HEAD", "options": "OPTIONS",
}

# Go HTTP route registrations.
#   chi:    r.Get("/users/{id}", getUser) / r.Method("POST", "/x", h)
#   gin:    r.GET("/items", h) / r.Handle("PUT", "/x", h)
#   echo:   e.GET("/x", h)
#   stdlib: http.HandleFunc("/x", h)  mux.HandleFunc(...)  mux.Handle("/x", h)
# Receiver is any identifier — chi/gin code uses r, mux, router, app, srv, etc.
_GO_METHOD_ROUTE_RE = re.compile(
    r"\b(?P<recv>[A-Za-z_]\w*)\."
    r"(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|"
    r"Get|Post|Put|Patch|Delete|Head|Options|Connect|Trace)\s*\(\s*"
    r"[\"`](?P<path>[^\"`]+)[\"`]\s*,\s*"
    r"(?P<handler>[A-Za-z_][\w.]*)",
)
# chi's r.Method("POST", "/x", h)  and  gin's r.Handle("POST", "/x", h)
_GO_GENERIC_METHOD_RE = re.compile(
    r"\b(?P<recv>[A-Za-z_]\w*)\.(?:Method|Handle)\s*\(\s*"
    r"[\"`](?P<verb>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)[\"`]\s*,\s*"
    r"[\"`](?P<path>[^\"`]+)[\"`]\s*,\s*"
    r"(?P<handler>[A-Za-z_][\w.]*)",
)
# stdlib net/http:  http.HandleFunc("/x", h) / mux.HandleFunc(...) / mux.Handle("/x", h)
_GO_STDLIB_HANDLE_RE = re.compile(
    r"\b(?P<recv>[A-Za-z_]\w*)\.(?:HandleFunc|Handle)\s*\(\s*"
    r"[\"`](?P<path>[^\"`]+)[\"`]\s*,\s*"
    r"(?P<handler>[A-Za-z_][\w.]*)",
)
_GO_VERB_NORMALIZE: dict[str, str] = {
    "GET": "GET", "POST": "POST", "PUT": "PUT", "PATCH": "PATCH",
    "DELETE": "DELETE", "HEAD": "HEAD", "OPTIONS": "OPTIONS",
    "CONNECT": "CONNECT", "TRACE": "TRACE",
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


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


class TreeSitterExtractor:
    """Extracts nodes + edges from a repo using graphify's tree-sitter parser."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def run(self) -> ExtractionResult:
        from orgraph._vendor.extract import extract

        files = _walk_code_files(self.repo_path)
        if not files:
            return ExtractionResult(extractor="treesitter")

        raw = extract(files, cache_root=self.repo_path, parallel=True)
        result = self._convert(raw)
        # Type-resolution pass: rewrite receiver-typed / super() calls to their
        # compiler-correct targets (graphify only name-matches). Mutates result.edges.
        from orgraph.extract.pyresolve import resolve_python_calls
        resolve_python_calls(result, files)
        return result

    def _convert(self, raw: dict) -> ExtractionResult:
        raw_nodes: list[dict] = raw.get("nodes", [])
        raw_edges: list[dict] = raw.get("edges", [])
        class_routes = self._collect_falcon_routes()
        fastify_routes = self._collect_fastify_routes()
        go_routes = self._collect_go_http_routes()

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
        id_to_path: dict[str, str] = {}   # raw id → abs file path (incl. bare file nodes)
        module_to_path: dict[str, str] = {}   # module stem (e.g. "handlers") → abs file path
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
            id_to_path[n["id"]] = abs_path

            # Skip bare file nodes (label ends with .py/.js etc.) — we generate File nodes in builder
            if "." in name and name.count(".") == 1 and not name.startswith("."):
                id_to_uid[n["id"]] = make_uid(name, abs_path, 0)
                # Record module stem → file path so IMPORTS edges that reference a
                # bare module name (graphify's "handlers", "models") resolve to a file.
                if abs_path:
                    module_to_path[Path(name).stem] = abs_path
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
                    http_path = class_routes.get(class_name, "")
            elif is_func and lang in {"typescript", "javascript"}:
                if name in fastify_routes:
                    http_method, http_path = fastify_routes[name]
            elif is_func and lang == "go":
                bare = name.split(".")[-1]
                if bare in go_routes:
                    http_method, http_path = go_routes[bare]

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
            relation = _RELATION_MAP.get(e.get("relation", ""), "CALLS")
            # IMPORTS is resolved separately into File→File edges (graphify import
            # edges reference bare module names that don't map to symbol uids).
            if relation == "IMPORTS":
                continue

            src_id = e.get("source", "")
            dst_id = e.get("target", "")
            src_uid = id_to_uid.get(src_id)
            dst_uid = id_to_uid.get(dst_id)
            if not src_uid or not dst_uid or src_uid == dst_uid:
                continue

            confidence = e.get("confidence", "INFERRED")
            line_no = 0
            if src_loc := e.get("source_location", ""):
                if src_loc.startswith("L"):
                    try:
                        line_no = int(src_loc[1:])
                    except ValueError:
                        pass

            edges.append({
                "source_uid": src_uid,
                "target_uid": dst_uid,
                "relation": relation,
                "confidence": confidence,
                "line_number": line_no,
            })

        edges.extend(self._resolve_import_edges(raw_edges, id_to_path, module_to_path))
        edges.extend(self._extract_celery_dispatch_edges(nodes))
        return ExtractionResult(nodes=nodes, edges=edges, extractor="treesitter")

    def _resolve_import_edges(
        self, raw_edges: list[dict], id_to_path: dict[str, str], module_to_path: dict[str, str],
    ) -> list[EdgeDict]:
        """Resolve graphify import edges to deduped File→File coupling edges.

        graphify emits imports as a mix of (file → module-name) and
        (module-name → symbol) where module names are bare strings, not node ids.
        We resolve both endpoints to a file path — via the node-id map (files +
        symbols carry a path) or the module-stem map — and emit one IMPORTS edge per
        importing/defining file pair. This is the file-level coupling `deps` reports.
        """
        def to_path(token: str) -> str:
            # Prefer the node-id map (files + symbols carry their real path); the
            # module-stem map is a fallback for bare module-name tokens.
            # Known limitation: module_to_path is keyed by file *stem*, so two files
            # sharing a stem in different dirs (e.g. users/models.py, orders/models.py)
            # collide — a stem-fallback resolution then picks one. id_to_path covers
            # the common cases; revisit by keying on the dotted module path if this
            # ever loses real coupling.
            if token in id_to_path:
                return id_to_path[token]
            return module_to_path.get(token) or module_to_path.get(token.split(".")[-1], "")

        out: list[EdgeDict] = []
        seen: set[tuple[str, str]] = set()
        for e in raw_edges:
            if _RELATION_MAP.get(e.get("relation", ""), "") != "IMPORTS":
                continue
            sp = to_path(e.get("source", ""))
            dp = to_path(e.get("target", ""))
            if not sp or not dp or sp == dp:
                continue
            key = (sp, dp)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "source_uid": "", "target_uid": "", "relation": "IMPORTS",
                "confidence": "INFERRED", "line_number": 0,
                "src_path": sp, "dst_path": dp, "alias": "",
            })
        return out

    def _collect_falcon_routes(self) -> dict[str, str]:
        routes: dict[str, str] = {}
        for path in _walk_code_files(self.repo_path):
            if path.suffix != ".py":
                continue
            source = _read_text(str(path))
            for match in _FALCON_ROUTE_RE.finditer(source):
                class_name = match.group("class").split(".")[-1]
                routes.setdefault(class_name, match.group("path"))
            for match in _FALCON_ROUTE_CONCAT_RE.finditer(source):
                class_name = match.group("class").split(".")[-1]
                # prefix is dynamic (e.g. settings.API_PREFIX); record the suffix only
                routes.setdefault(class_name, "{prefix}" + match.group("path"))
        return routes

    def _collect_go_http_routes(self) -> dict[str, tuple[str, str]]:
        """Return bare_handler_name → (HTTP_METHOD, path) for Go HTTP frameworks.

        Recognises chi (`r.Get("/x", h)`, `r.Method("POST", "/x", h)`), gin
        (`r.GET("/x", h)`, `r.Handle("POST", "/x", h)`), echo, and stdlib
        `net/http` (`http.HandleFunc("/x", h)`, `mux.Handle("/x", h)`). stdlib
        routes don't have a verb at registration — they're recorded as `"ANY"`.
        """
        routes: dict[str, tuple[str, str]] = {}
        # Don't collide with Go's own `http` package method names — bag of
        # receivers that we *won't* treat as route registrars even when they
        # match the verb-method regex (e.g. `http.Get(url)` is an HTTP client).
        _NOT_ROUTER = {"http"}

        for path in _walk_code_files(self.repo_path):
            if path.suffix != ".go":
                continue
            source = _read_text(str(path))
            # Strip Go line comments (`// ...`) so commented-out or documented
            # route examples don't poison the registry. Block comments are left
            # alone — they rarely contain ready-to-match registration syntax.
            source = re.sub(r"//[^\n]*", "", source)

            # generic dispatcher first (more specific) so a matching `Handle`
            # with a verb doesn't get re-matched as a "method-named" call below.
            consumed_spans: list[tuple[int, int]] = []
            for match in _GO_GENERIC_METHOD_RE.finditer(source):
                handler = match.group("handler").split(".")[-1]
                routes.setdefault(handler, (_GO_VERB_NORMALIZE[match.group("verb")], match.group("path")))
                consumed_spans.append(match.span())

            def _consumed(pos: int) -> bool:
                return any(s <= pos < e for s, e in consumed_spans)

            for match in _GO_METHOD_ROUTE_RE.finditer(source):
                if _consumed(match.start()):
                    continue
                if match.group("recv") in _NOT_ROUTER:
                    continue
                method = _GO_VERB_NORMALIZE.get(match.group("method").upper())
                if not method:
                    continue
                handler = match.group("handler").split(".")[-1]
                routes.setdefault(handler, (method, match.group("path")))

            for match in _GO_STDLIB_HANDLE_RE.finditer(source):
                if _consumed(match.start()):
                    continue
                handler = match.group("handler").split(".")[-1]
                routes.setdefault(handler, ("ANY", match.group("path")))

        return routes

    def _collect_fastify_routes(self) -> dict[str, tuple[str, str]]:
        """Return bare_handler_name → (HTTP_METHOD, path) for Fastify/Express routes.

        Scans .ts / .js files for patterns like:
            plugin.get('/path', controller.handler)
            plugin.post('/path', { preHandler: [...] }, controller.handler)
        The handler name is normalised to its bare suffix (after the last dot).
        """
        routes: dict[str, tuple[str, str]] = {}
        for path in _walk_code_files(self.repo_path):
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
                continue
            source = _read_text(str(path))
            for match in _FASTIFY_ROUTE_RE.finditer(source):
                method = _FASTIFY_HTTP[match.group("method").lower()]
                route_path = match.group("path")
                handler = match.group("handler").split(".")[-1]  # bare name
                routes.setdefault(handler, (method, route_path))
        return routes

    def _extract_celery_dispatch_edges(self, nodes: list[NodeDict]) -> list[EdgeDict]:
        functions = [n for n in nodes if n.get("label") == "Function" and n.get("uid")]
        by_name: dict[str, NodeDict] = {}
        by_path: dict[str, list[NodeDict]] = {}

        for node in functions:
            name = node.get("name", "")
            if not name:
                continue
            by_name.setdefault(name, node)
            by_name.setdefault(name.split(".")[-1], node)
            by_path.setdefault(node.get("path", ""), []).append(node)

        for path_nodes in by_path.values():
            path_nodes.sort(key=lambda n: n.get("line_number", 0))

        edges: list[EdgeDict] = []
        seen: set[tuple[str, str, int]] = set()
        for path, path_nodes in by_path.items():
            if not path or not path.endswith(".py"):
                continue
            source = _read_text(path)
            if not source:
                continue
            for match in _CELERY_DISPATCH_RE.finditer(source):
                line_start = source.rfind("\n", 0, match.start()) + 1
                if "#" in source[line_start:match.start()]:
                    continue  # dispatch sits after '#' on its line → commented-out code
                line_no = _line_number(source, match.start())
                target_name = match.group("target").split(".")[-1]
                target = by_name.get(target_name)
                caller = self._enclosing_function(path_nodes, line_no)
                if not caller or not target:
                    continue
                src_uid = caller.get("uid", "")
                dst_uid = target.get("uid", "")
                key = (src_uid, dst_uid, line_no)
                if not src_uid or not dst_uid or src_uid == dst_uid or key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "source_uid": src_uid,
                    "target_uid": dst_uid,
                    "relation": "CALLS",
                    "confidence": "INFERRED",
                    "line_number": line_no,
                    "call_kind": CALL_KIND_CELERY,
                })
        return edges

    @staticmethod
    def _enclosing_function(nodes: list[NodeDict], line_no: int) -> NodeDict | None:
        caller: NodeDict | None = None
        for node in nodes:
            node_line = node.get("line_number", 0)
            if node_line and node_line < line_no:
                caller = node
            elif node_line >= line_no:
                break
        return caller
