"""Ground-truth call-graph fixtures — the correctness contract for extraction.

Each fixture is a tiny self-contained Python project whose COMPLETE set of true
intra-project CALLS edges is known by hand (by reading the source, independent of
any tool). The harness/test runs an extractor over the fixture and checks:

  - recall:   every edge in `true_edges` is present
  - precision: no edge in `forbidden_edges` is present

Edge naming matches orgraph node `name`: a free function is its bare name
(``"helper"``); a method is ``"ClassName.method"`` (the qualified form
``TreeSitterExtractor._convert`` emits).

`xfail=True` marks a pattern the current extractor is not expected to get right
yet (type-resolution-dependent or deferred). The pytest gate xfails these
non-strictly, so the suite stays green; as the resolver lands a pattern it
XPASSes (visible) and its `xfail` is flipped to a hard assertion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CallGraphFixture:
    id: str
    modules: dict[str, str]
    true_edges: frozenset[tuple[str, str]]
    forbidden_edges: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    xfail: bool = False
    note: str = ""


def found_calls(result) -> set[tuple[str, str]]:
    """Collect (caller_name, callee_name) CALLS pairs from an ExtractionResult."""
    name = {n["uid"]: n["name"] for n in result.nodes}
    out: set[tuple[str, str]] = set()
    for e in result.edges:
        if e.get("relation") != "CALLS":
            continue
        s, d = name.get(e.get("source_uid")), name.get(e.get("target_uid"))
        if s and d:
            out.add((s, d))
    return out


def write_fixture(fx: CallGraphFixture, root: Path) -> Path:
    """Materialise a fixture's modules under `root` and return the repo dir."""
    repo = root / fx.id
    repo.mkdir(parents=True, exist_ok=True)
    for fname, src in fx.modules.items():
        (repo / fname).write_text(src, encoding="utf-8")
    return repo


def _f(s: str) -> str:
    # Strip the leading newline so triple-quoted bodies can start on the next line.
    return s[1:] if s.startswith("\n") else s


FIXTURES: list[CallGraphFixture] = [
    # ── assert-now: tree-sitter already handles these (the 4/6 baseline) ──────
    CallGraphFixture(
        id="free_call",
        modules={"m.py": _f("""
def helper():
    return 1

def caller():
    helper()
    return 2
""")},
        true_edges=frozenset({("caller", "helper")}),
    ),
    CallGraphFixture(
        id="self_method",
        modules={"m.py": _f("""
class Service:
    def run(self):
        self.step()

    def step(self):
        return 3
""")},
        true_edges=frozenset({("Service.run", "Service.step")}),
    ),
    CallGraphFixture(
        id="method_to_free",
        modules={"m.py": _f("""
def helper():
    return 1

class Service:
    def run(self):
        helper()
""")},
        true_edges=frozenset({("Service.run", "helper")}),
    ),
    CallGraphFixture(
        id="cross_module",
        modules={
            "mod_a.py": _f("""
def helper():
    return 1
"""),
            "mod_b.py": _f("""
from mod_a import helper

def orchestrate():
    helper()
"""),
        },
        true_edges=frozenset({("orchestrate", "helper")}),
    ),

    # ── assert-now negatives: must NOT appear, satisfied today ────────────────
    CallGraphFixture(
        id="commented_call",
        modules={"m.py": _f("""
def ghost():
    return 9

def caller():
    # ghost()
    return 2
""")},
        true_edges=frozenset(),
        forbidden_edges=frozenset({("caller", "ghost")}),
    ),
    CallGraphFixture(
        id="string_literal_call",
        modules={"m.py": _f("""
def ghost():
    return 9

def caller():
    x = "ghost()"
    return x
""")},
        true_edges=frozenset(),
        forbidden_edges=frozenset({("caller", "ghost")}),
    ),

    # ── resolver targets: xfail now, flipped to assert in Phase D ─────────────
    CallGraphFixture(
        id="super_basic",
        modules={"m.py": _f("""
class Base:
    def setup(self):
        return 0

class Derived(Base):
    def setup(self):
        super().setup()
""")},
        true_edges=frozenset({("Derived.setup", "Base.setup")}),
        note="resolved: super() via class bases",
    ),
    CallGraphFixture(
        id="receiver_typed",
        modules={
            "mod_a.py": _f("""
class Service:
    def run(self):
        return 1
"""),
            "mod_b.py": _f("""
from mod_a import Service

def orchestrate():
    s = Service()
    s.run()
"""),
        },
        true_edges=frozenset({("orchestrate", "Service.run")}),
        note="resolved: constructor-inferred local type binding",
    ),
    CallGraphFixture(
        id="self_attr_method",
        modules={"m.py": _f("""
class Dep:
    def go(self):
        return 1

class Service:
    def __init__(self):
        self.dep = Dep()

    def run(self):
        self.dep.go()
""")},
        true_edges=frozenset({("Service.run", "Dep.go")}),
        note="resolved: self.attr type binding from __init__",
    ),
    CallGraphFixture(
        id="two_classes_same_method",
        modules={"m.py": _f("""
class A:
    def run(self):
        return 1

class B:
    def run(self):
        return 2

def caller():
    a = A()
    a.run()
""")},
        true_edges=frozenset({("caller", "A.run")}),
        forbidden_edges=frozenset({("caller", "B.run")}),
        note="resolved: receiver type disambiguates A.run from B.run",
    ),

    # ── deferred: xfail (resolver does not handle these in the 80/20 scope) ────
    CallGraphFixture(
        id="super_multi_inheritance",
        modules={"m.py": _f("""
class A:
    def m(self):
        return 1

class B:
    def m(self):
        return 2

class C(A, B):
    def m(self):
        super().m()
""")},
        true_edges=frozenset({("C.m", "A.m")}),
        xfail=True,
        note="deferred: full MRO linearization",
    ),
    CallGraphFixture(
        id="super_explicit",
        modules={"m.py": _f("""
class Base:
    def setup(self):
        return 0

class Derived(Base):
    def setup(self):
        super(Derived, self).setup()
""")},
        true_edges=frozenset({("Derived.setup", "Base.setup")}),
        xfail=True,
        note="deferred: super(Cls, self) form",
    ),
    CallGraphFixture(
        id="receiver_chained",
        modules={"m.py": _f("""
class Service:
    def run(self):
        return 1

def caller():
    Service().run()
""")},
        true_edges=frozenset({("caller", "Service.run")}),
        xfail=True,
        note="deferred: chained Class().method()",
    ),
    CallGraphFixture(
        id="receiver_from_return",
        modules={"m.py": _f("""
class Service:
    def run(self):
        return 1

def make():
    return Service()

def caller():
    s = make()
    s.run()
""")},
        true_edges=frozenset({("caller", "Service.run")}),
        xfail=True,
        note="deferred: cross-function return-type inference",
    ),
    CallGraphFixture(
        id="receiver_type_hint",
        modules={"m.py": _f("""
class Service:
    def run(self):
        return 1

def caller(s: Service):
    s.run()
""")},
        true_edges=frozenset({("caller", "Service.run")}),
        xfail=True,
        note="deferred: parameter type-hint binding",
    ),
    CallGraphFixture(
        id="aliased_import",
        modules={
            "mod_a.py": _f("""
def helper():
    return 1
"""),
            "mod_b.py": _f("""
from mod_a import helper as h

def caller():
    h()
"""),
        },
        true_edges=frozenset({("caller", "helper")}),
        xfail=True,
        note="deferred: import-alias resolution",
    ),
    CallGraphFixture(
        id="classmethod_call",
        modules={"m.py": _f("""
class Service:
    @classmethod
    def create(cls):
        return 1

def caller():
    Service.create()
""")},
        true_edges=frozenset({("caller", "Service.create")}),
        xfail=True,
        note="deferred: classmethod call on class name",
    ),
    CallGraphFixture(
        id="staticmethod_call",
        modules={"m.py": _f("""
class Service:
    @staticmethod
    def util():
        return 1

def caller():
    Service.util()
""")},
        true_edges=frozenset({("caller", "Service.util")}),
        xfail=True,
        note="deferred: staticmethod call on class name",
    ),
    CallGraphFixture(
        id="nested_function",
        modules={"m.py": _f("""
def outer():
    def inner():
        return 1
    inner()
""")},
        true_edges=frozenset({("outer", "inner")}),
        xfail=True,
        note="deferred: nested function scoping",
    ),
    CallGraphFixture(
        id="module_qualified",
        modules={
            "mod_a.py": _f("""
def helper():
    return 1
"""),
            "mod_b.py": _f("""
import mod_a

def caller():
    mod_a.helper()
"""),
        },
        true_edges=frozenset({("caller", "helper")}),
        xfail=True,
        note="deferred: module-qualified call",
    ),
    CallGraphFixture(
        id="decorated_method",
        modules={"m.py": _f("""
def deco(fn):
    return fn

class Service:
    @deco
    def run(self):
        self.step()

    def step(self):
        return 1
""")},
        true_edges=frozenset({("Service.run", "Service.step")}),
        xfail=True,
        note="deferred: decorated method body resolution",
    ),

    # ── deferred negatives: flow sensitivity not implemented ──────────────────
    CallGraphFixture(
        id="shadowed_var",
        modules={"m.py": _f("""
class Service:
    def run(self):
        return 1

class Other:
    def run(self):
        return 2

def caller():
    s = Service()
    s = Other()
    s.run()
""")},
        true_edges=frozenset({("caller", "Other.run")}),
        forbidden_edges=frozenset({("caller", "Service.run")}),
        xfail=True,
        note="deferred: flow-sensitive reassignment",
    ),
    CallGraphFixture(
        id="dead_code_after_return",
        modules={"m.py": _f("""
def ghost():
    return 9

def caller():
    return 1
    ghost()
""")},
        true_edges=frozenset(),
        forbidden_edges=frozenset({("caller", "ghost")}),
        xfail=True,
        note="deferred: dead-code elimination after return",
    ),
]
