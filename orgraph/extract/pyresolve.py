"""Lightweight Python type-resolution pass over the tree-sitter call graph.

graphify resolves calls by name only, so it misses (or mis-targets) calls that
need to know a receiver's type:

  - receiver-typed:  ``s = Service(); s.run()``      → ``Service.run``
  - self-attribute:  ``self.dep = Dep(); self.dep.go()`` → ``Dep.go``
  - super:           ``super().setup()``              → ``Base.setup``

This module re-parses each Python file with tree-sitter-python (already a
dependency), recovers *local, constructor-inferred* type bindings, and rewrites
the corresponding CALLS edges to the type-correct target. It is intentionally an
80/20 pass — no fixpoint, flow-sensitivity, cross-file return-type inference, or
full MRO. Those remain name-matched (and are tracked as xfail fixtures).

Clean-room implementation of standard static-analysis techniques (constructor
inference, receiver-constrained resolution); no third-party resolver code is used.
"""
from __future__ import annotations

from pathlib import Path

from orgraph.extract.types import EdgeDict, ExtractionResult, make_uid

try:
    import tree_sitter_python as _tspython
    from tree_sitter import Language as _Language, Parser as _Parser
    _PY_LANG = _Language(_tspython.language())
except Exception:  # pragma: no cover - grammar always shipped, but degrade safely
    _PY_LANG = None


def _text(node) -> str:
    return node.text.decode("utf-8", "replace")


def _last_segment(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def _is_constructor_call(node) -> str | None:
    """If `node` is a call to a Class-like name, return that name, else None."""
    if node is None or node.type != "call":
        return None
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        name = _text(fn)
    elif fn.type == "attribute":  # models.User(...)
        name = _last_segment(_text(fn))
    else:
        return None
    return name if name[:1].isupper() else None


def _class_bases(class_node) -> list[str]:
    supers = class_node.child_by_field_name("superclasses")
    if supers is None:
        return []
    bases: list[str] = []
    for ch in supers.named_children:
        if ch.type in ("identifier", "attribute"):
            bases.append(_last_segment(_text(ch)))
    return bases


def _collect_bindings(body_node) -> tuple[dict[str, str], dict[str, str]]:
    """Walk a subtree collecting constructor-inferred bindings.

    Returns (local_types, self_attr_types):
      local_types[var] = ClassName        from `var = ClassName(...)`
      self_attr_types[attr] = ClassName    from `self.attr = ClassName(...)`
    Descends through statements but not into nested function/class definitions
    (their scopes are separate; nested handling is deferred).
    """
    local_types: dict[str, str] = {}
    self_attrs: dict[str, str] = {}
    ambiguous_local: set[str] = set()
    ambiguous_attr: set[str] = set()

    def bind(store: dict[str, str], ambiguous: set[str], key: str, cls: str) -> None:
        # Reassignment to a *different* class makes the binding ambiguous; we
        # can't pick a type flow-insensitively, so we drop it (prefer a missing
        # edge over a misleading one) rather than letting last-write-wins emit a
        # confidently-wrong edge for the earlier call sites.
        if key in store and store[key] != cls:
            ambiguous.add(key)
        store[key] = cls

    def walk(n):
        for ch in n.named_children:
            if ch.type in ("function_definition", "class_definition", "lambda"):
                continue  # separate scope
            if ch.type == "assignment":
                cls = _is_constructor_call(ch.child_by_field_name("right"))
                left = ch.child_by_field_name("left")
                if cls and left is not None:
                    if left.type == "identifier":
                        bind(local_types, ambiguous_local, _text(left), cls)
                    elif left.type == "attribute":
                        obj = left.child_by_field_name("object")
                        attr = left.child_by_field_name("attribute")
                        if obj is not None and attr is not None and _text(obj) == "self":
                            bind(self_attrs, ambiguous_attr, _text(attr), cls)
            walk(ch)

    walk(body_node)
    for k in ambiguous_local:
        local_types.pop(k, None)
    for k in ambiguous_attr:
        self_attrs.pop(k, None)
    return local_types, self_attrs


def _resolve_target(call_node, local_types: dict[str, str],
                    self_attrs: dict[str, str], bases: list[str]) -> str | None:
    """Return the qualified target name (`ClassName.method`) for a receiver call, or None."""
    fn = call_node.child_by_field_name("function")
    if fn is None or fn.type != "attribute":
        return None  # free call — graphify already name-matches it
    method = fn.child_by_field_name("attribute")
    recv = fn.child_by_field_name("object")
    if method is None or recv is None:
        return None
    meth = _text(method)

    # super().method()
    if recv.type == "call":
        rfn = recv.child_by_field_name("function")
        if rfn is not None and rfn.type == "identifier" and _text(rfn) == "super":
            return f"{bases[0]}.{meth}" if bases else None

    # var.method()  — var bound to a constructor result
    if recv.type == "identifier":
        cls = local_types.get(_text(recv))
        return f"{cls}.{meth}" if cls else None

    # self.attr.method()  — attr bound in the class
    if recv.type == "attribute":
        obj = recv.child_by_field_name("object")
        attr = recv.child_by_field_name("attribute")
        if obj is not None and attr is not None and _text(obj) == "self":
            cls = self_attrs.get(_text(attr))
            return f"{cls}.{meth}" if cls else None
    return None


def resolve_python_calls(result: ExtractionResult, files: list[Path]) -> None:
    """Rewrite receiver-typed / super() CALLS edges in `result` (mutates in place)."""
    if _PY_LANG is None:
        return
    py_files = [f for f in files if f.suffix == ".py"]
    if not py_files:
        return

    # Index existing symbol nodes: qualified name → uid, and per-file (line, uid)
    # for enclosing-caller lookup.
    name_to_uid: dict[str, str] = {}
    by_path: dict[str, list[tuple[int, str]]] = {}
    for n in result.nodes:
        nm, uid, path, line = n.get("name"), n.get("uid"), n.get("path"), n.get("line_number", 0)
        if not nm or not uid:
            continue
        name_to_uid.setdefault(nm, uid)
        if path:
            by_path.setdefault(path, []).append((line, uid))
    for entries in by_path.values():
        entries.sort()

    def enclosing_uid(path: str, line: int) -> str | None:
        best = None
        for ln, uid in by_path.get(path, ()):
            if ln <= line:
                best = uid
            else:
                break
        return best

    parser = _Parser(_PY_LANG)
    resolved: list[tuple[str, str, int, str]] = []   # (caller_uid, target_uid, line, bare_method)

    for f in py_files:
        try:
            src = f.read_bytes()
        except OSError:
            continue
        abs_path = str(f.resolve()) if f.exists() else str(f)
        root = parser.parse(src).root_node

        # Pre-pass: self.attr types per class node (id-keyed) for the file.
        _class_self_attrs: dict[int, dict[str, str]] = {}

        def gather_classes(node):
            for ch in node.named_children:
                if ch.type == "class_definition":
                    _, self_attrs = _collect_bindings(ch)
                    _class_self_attrs[id(ch)] = self_attrs
                gather_classes(ch)

        def visit2(node, bases, class_node):
            for ch in node.named_children:
                if ch.type == "class_definition":
                    visit2(ch, _class_bases(ch), ch)
                elif ch.type == "function_definition":
                    local_types, _ = _collect_bindings(ch)
                    self_attrs = _class_self_attrs.get(id(class_node), {}) if class_node is not None else {}
                    _resolve_calls_in(ch, local_types, self_attrs, bases, abs_path)
                    visit2(ch, bases, class_node)
                else:
                    visit2(ch, bases, class_node)

        def _resolve_calls_in(func_node, local_types, self_attrs, bases, path):
            def walk_calls(n):
                for ch in n.named_children:
                    if ch.type in ("function_definition", "class_definition"):
                        continue  # nested scope deferred
                    if ch.type == "call":
                        target = _resolve_target(ch, local_types, self_attrs, bases)
                        if target:
                            line = ch.start_point[0] + 1
                            tgt_uid = name_to_uid.get(target) or _same_file_pick(target, path)
                            caller_uid = enclosing_uid(path, line)
                            if tgt_uid and caller_uid and tgt_uid != caller_uid:
                                resolved.append((caller_uid, tgt_uid, line, _last_segment(target)))
                    walk_calls(ch)
            walk_calls(func_node)

        def _same_file_pick(qual_name: str, path: str) -> str | None:
            # collision fallback: prefer a node with this name defined in `path`
            for n in result.nodes:
                if n.get("name") == qual_name and n.get("path") == path and n.get("uid"):
                    return n["uid"]
            return None

        gather_classes(root)
        visit2(root, [], None)

    if not resolved:
        return

    # Dedup + supersede: drop name-matched CALLS edges the resolver corrects, then
    # add the type-correct edges (deduped by caller/target/line).
    uid_to_bare = {n["uid"]: _last_segment(n.get("name", "")) for n in result.nodes if n.get("uid")}
    superseded = {(c, m) for c, _t, _l, m in resolved}

    kept: list[EdgeDict] = []
    for e in result.edges:
        if e.get("relation") == "CALLS":
            c, d = e.get("source_uid"), e.get("target_uid")
            if (c, uid_to_bare.get(d, "")) in superseded:
                continue  # superseded by a resolved, type-correct edge
        kept.append(e)

    seen: set[tuple[str, str, int]] = set()
    for caller_uid, tgt_uid, line, _bare in resolved:
        key = (caller_uid, tgt_uid, line)
        if key in seen:
            continue
        seen.add(key)
        kept.append({
            "source_uid": caller_uid, "target_uid": tgt_uid, "relation": "CALLS",
            "confidence": "INFERRED", "line_number": line, "call_kind": "resolved",
        })

    result.edges = kept
