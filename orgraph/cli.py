"""orgraph CLI — index / serve / search / status / eval."""
from __future__ import annotations

import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _orgraph_dir(repo_path: Path) -> Path:
    return repo_path / ".orgraph"



@click.group()
@click.version_option(package_name="orgraph-mcp")
def main() -> None:
    """Codebase knowledge graph for coding agents."""


@main.command()
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--force", is_flag=True, help="Re-index all files, ignoring the manifest.")
def index(repo_path: str, force: bool) -> None:
    """Index a repo: extract nodes/edges, build topology + communities, store in Kuzu graph."""
    from orgraph.extract.manifest import Manifest
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.pipeline import build_index

    repo = Path(repo_path).resolve()
    orgraph_dir = _orgraph_dir(repo)
    orgraph_dir.mkdir(parents=True, exist_ok=True)

    manifest = Manifest(orgraph_dir)
    if not force:
        manifest.load()

    t0 = time.perf_counter()
    console.print(f"[bold cyan]orgraph[/] indexing [yellow]{repo}[/]")

    db_path = orgraph_dir / "graph.kuzu"
    db = OrgraphDB(db_path)
    try:
        with console.status("Building index (extract → graph → topology → communities → search)…"):
            stats = build_index(db, repo, orgraph_dir, rebuild_search=True)
    finally:
        db.close()

    console.print(
        f"  [green]✓[/] Extraction ({stats['extractor']}): "
        f"{stats['node_count']} nodes, {stats['edge_count']} edges"
    )
    console.print(
        f"  [green]✓[/] Topology: [bold]{stats['clusters']}[/] clusters"
        f" ({'with' if stats['foundational'] else 'no'} foundational)"
    )
    console.print(f"  [green]✓[/] Communities: [bold]{stats['communities']}[/] (Leiden/Louvain)")
    console.print("  [green]✓[/] Search index built (.orgraph/search/)")

    for warning in stats.get("warnings", []):
        console.print(f"  [yellow]⚠[/]  {warning}")

    # Nudge: if we used tree-sitter but a SCIP indexer exists for this language,
    # point the user at the higher-precision (compiler-resolved) call graph.
    if stats.get("extractor") == "treesitter":
        from orgraph.extract.scip import _binary_for_lang, _detect_primary_lang, scip_install_hint
        lang = _detect_primary_lang(repo)
        if lang and not _binary_for_lang(lang):
            hint = scip_install_hint(lang)
            if hint:
                console.print(
                    f"  [dim]tip: for a higher-precision call graph, install [bold]{hint[0]}[/]"
                    f" ([italic]{hint[1]}[/]) — orgraph will use it automatically.[/]"
                )

    # --- Manifest ---
    manifest.update(manifest.all_files(repo))
    manifest.save()

    elapsed = time.perf_counter() - t0
    console.print(
        f"\n[bold green]Done.[/] Indexed [bold]{stats['nodes']}[/] nodes, "
        f"[bold]{stats['edges']}[/] edges in [bold]{elapsed:.1f}s[/]"
    )
    console.print(f"  Graph at: [dim]{db_path}[/]")


@main.command()
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
def status(repo_path: str) -> None:
    """Show graph stats, topology clusters, and community count for an indexed repo."""
    from orgraph.topology.serialise import load_communities, load_topology

    repo = Path(repo_path).resolve()
    orgraph_dir = _orgraph_dir(repo)
    db_path = orgraph_dir / "graph.kuzu"

    if not db_path.exists():
        console.print("[red]Not indexed yet. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.graph import query as gq

    with open_db_readonly(db_path) as db:
        node_counts = gq.get_node_counts(db)
        edge_counts = gq.get_edge_counts(db)

    table = Table(title=f"orgraph status — {repo.name}", show_header=True)
    table.add_column("Label", style="cyan")
    table.add_column("Count", justify="right", style="bold")

    for label, cnt in node_counts.items():
        table.add_row(label, str(cnt))
    table.add_row("─" * 12, "─" * 6)
    table.add_row("Total nodes", str(sum(node_counts.values())))

    for rel, cnt in edge_counts.items():
        table.add_row(f"  [{rel}]", str(cnt))
    table.add_row("Total edges", str(sum(edge_counts.values())))

    console.print(table)

    # --- Topology clusters ---
    topology = load_topology(orgraph_dir)
    if topology and topology.clusters:
        cluster_table = Table(title="Topology Clusters", show_header=True)
        cluster_table.add_column("Cluster ID", style="cyan", max_width=40)
        cluster_table.add_column("Files", justify="right")
        cluster_table.add_column("Depth", justify="right")
        cluster_table.add_column("Avg Indegree", justify="right")
        cluster_table.add_column("Foundational", justify="center")

        for c in topology.clusters[:20]:  # cap at 20 for readability
            depth_str = f"{c.min_depth}–{c.max_depth}" if c.min_depth < 999 else "n/a"
            cluster_table.add_row(
                c.cluster_id,
                str(len(c.all_files)),
                depth_str,
                f"{c.avg_indegree:.1f}",
                "✓" if c.is_foundational else "",
            )
        if len(topology.clusters) > 20:
            cluster_table.add_row(f"… {len(topology.clusters) - 20} more", "", "", "", "")

        console.print(cluster_table)
    else:
        console.print("[dim]No topology data. Re-run `orgraph index` to build it.[/]")

    # --- Community count ---
    communities = load_communities(orgraph_dir)
    if communities is not None:
        sizes = sorted((len(v) for v in communities.values()), reverse=True)
        top5 = ", ".join(str(s) for s in sizes[:5])
        console.print(
            f"\n[bold]Leiden communities:[/] {len(communities)} total  "
            f"(top 5 sizes: {top5})"
        )
    else:
        console.print("[dim]No community data. Re-run `orgraph index` to build it.[/]")


@main.command()
@click.argument("query")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--top-k", default=10, show_default=True)
def search(query: str, repo_path: str, top_k: int) -> None:
    """Find code by describing what it does — semantic + keyword search.

    Understands meaning, not just exact strings. Write a descriptive phrase,
    not a function name. Results are ranked ●●●/●●○/●○○ by relevance.

    \b
    OPTIONS:
      --top-k   How many results to return (default 10). Lower it to cut noise,
                raise it if you suspect the right result is buried.

    \b
    EXAMPLES:
      orgraph search "coupon validation logic" .
      orgraph search "order cancellation refund" .
      orgraph search "birthday coupon auto apply" . --top-k 5

    \b
    TIPS:
      - Use a phrase, not a single word. "theme sync" beats "theme".
      - Top results (●●●) are reliable. Below ●●○ treat as noise.
      - Use this to find where something lives, then use `file` or open it.
    """
    from orgraph.graph import query as gq
    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.search.index import SearchIndex

    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"

    idx = SearchIndex.load(repo)
    if idx is None:
        console.print("[red]Search index not built yet. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    results = idx.search(query, top_k=top_k)
    if not results:
        console.print("[yellow]No results.[/]")
        return

    repo_str = str(repo) + "/"
    top_score = results[0].score if results else 1.0

    def _tier(score: float) -> str:
        ratio = score / top_score if top_score else 0
        if ratio >= 0.70:
            return "[bold green]●●●[/]"
        if ratio >= 0.40:
            return "[yellow]●●○[/]"
        return "[dim]●○○[/]"

    with open_db_readonly(db_path) as db:
        for i, r in enumerate(results, 1):
            c = r.chunk
            rel_path = c.file_path.replace(repo_str, "")
            sym = gq.get_enclosing_symbol(db, c.file_path, c.start_line)
            sym_label = (
                f"[bold cyan]{sym['name']}()[/]  " if sym and sym["kind"] == "function"
                else f"[bold yellow]{sym['name']}[/]  " if sym
                else ""
            )
            console.print(
                f"{_tier(r.score)} [bold]{i}.[/]  {sym_label}"
                f"[green]{rel_path}[/]:[dim]{c.start_line}-{c.end_line}[/]"
            )
            snippet = c.content[:300].strip()
            for line in snippet.splitlines()[:6]:
                console.print(f"   [dim]{line}[/]")
            console.print()


@main.command()
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--ground-truth",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to ground truth JSON. Defaults to the bundled codewiki fixture.",
)
@click.option("--top-k", default=10, show_default=True)
@click.option("--output", default=None, help="Write JSON report to this path.")
def eval(repo_path: str, ground_truth: str | None, top_k: int, output: str | None) -> None:
    """Evaluate retrieval quality against a ground-truth Q&A set."""
    from orgraph.eval.runner import EvalRunner

    repo = Path(repo_path).resolve()

    if ground_truth is None:
        # Default to bundled codewiki fixture
        gt_path = Path(__file__).parent / "eval" / "fixtures" / "codewiki_gt.json"
    else:
        gt_path = Path(ground_truth).resolve()

    if not gt_path.exists():
        console.print(f"[red]Ground truth file not found: {gt_path}[/]")
        raise SystemExit(1)

    with console.status(f"Running eval on [yellow]{repo.name}[/] against {gt_path.name}…"):
        runner = EvalRunner(repo_path=repo, ground_truth_path=gt_path, top_k=top_k)
        try:
            report = runner.run()
        except RuntimeError as e:
            console.print(f"[red]{e}[/]")
            raise SystemExit(1)

    # Results table
    table = Table(title=f"Eval — {repo.name} / {gt_path.name}", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Score", justify="right", style="bold")

    table.add_row("NDCG@10 (all)", f"{report.ndcg_at_10:.3f}")
    table.add_row("MRR (all)", f"{report.mrr:.3f}")
    table.add_row("Precision@3 (all)", f"{report.precision_at_3:.3f}")
    table.add_row("Symbol MRR (all)", f"{report.symbol_mrr:.3f}")
    table.add_row("─" * 18, "─" * 6)
    table.add_row("Semantic NDCG@10", f"{report.semantic_ndcg:.3f}")
    table.add_row("Symbol query MRR", f"{report.symbol_query_mrr:.3f}")
    table.add_row("─" * 18, "─" * 6)
    table.add_row("Queries evaluated", str(report.query_count))

    console.print(table)

    # Per-query breakdown
    detail = Table(title="Per-query breakdown", show_header=True, show_lines=False)
    detail.add_column("ID", style="dim", max_width=25)
    detail.add_column("Type", style="dim")
    detail.add_column("NDCG", justify="right")
    detail.add_column("MRR", justify="right")
    detail.add_column("SymMRR", justify="right")

    for r in sorted(report.per_query, key=lambda x: -x.ndcg_at_10):
        color = "green" if r.ndcg_at_10 >= 0.5 else ("yellow" if r.ndcg_at_10 > 0 else "red")
        detail.add_row(
            r.query_id[:25],
            r.query_type,
            f"[{color}]{r.ndcg_at_10:.2f}[/]",
            f"{r.mrr:.2f}",
            f"{r.symbol_mrr:.2f}",
        )

    console.print(detail)

    if output:
        out_path = Path(output)
        report.save(out_path)
        console.print(f"\nReport saved to [dim]{out_path}[/]")


@main.command("who-calls")
@click.argument("symbol")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--depth", default=1, show_default=True, help="How many hops up the call chain.")
def who_calls(symbol: str, repo_path: str, depth: int) -> None:
    """Show every place SYMBOL is called — use before changing a function.

    Answers: "what breaks if I edit this?" High caller count = risky change.

    \b
    DEPTH: hops UP the call chain (who calls the callers).
      --depth 1 (default): direct callers only.
                           "What calls get_valid_coupon directly?"
      --depth 2:           callers of callers.
                           "What calls the things that call get_valid_coupon?"
      --depth 3:           three levels up — full upstream blast radius.
    Start at 1. Go deeper only if you need to understand the full chain.

    \b
    EXAMPLES:
      orgraph who-calls get_valid_coupon .
      orgraph who-calls build_order_model . --depth 2
      orgraph who-calls apply_coupon .
    """
    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"
    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.graph import query as gq

    with open_db_readonly(db_path) as db:
        roots = gq.resolve_symbol(db, symbol)
        if not roots:
            console.print(f"[red]Symbol '{symbol}' not found in index.[/]")
            raise SystemExit(1)

        target = roots[0]
        console.print(f"\n[bold cyan]Who calls[/] [bold yellow]{target['name']}[/]  [dim]{target['path']}:{target['line']}[/]\n")

        visited: set[str] = {target["uid"]}
        frontier = [(target["uid"], target["name"], 0)]
        all_callers: list[dict] = []

        while frontier:
            uid, name, d = frontier.pop(0)
            if d >= depth:
                continue
            for c in gq.get_call_edges(db, uid, "callers"):
                all_callers.append({**c, "callee": name, "depth": d + 1})
                if c["uid"] not in visited:
                    visited.add(c["uid"])
                    frontier.append((c["uid"], c["name"], d + 1))

    if not all_callers:
        console.print("[yellow]No callers found — this function is not called anywhere in the indexed code.[/]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Caller", style="cyan")
    table.add_column("File", style="green")
    table.add_column("Line", justify="right", style="bold")
    if depth > 1:
        table.add_column("Calls into", style="dim")

    repo_str = str(repo) + "/"
    for c in all_callers:
        rel_path = c["path"].replace(repo_str, "") if c["path"] else ""
        call_line = c.get("call_line") or c.get("line") or 0
        if depth > 1:
            table.add_row(c["name"], rel_path, str(call_line), c["callee"])
        else:
            table.add_row(c["name"], rel_path, str(call_line))

    console.print(table)
    console.print(f"\n[dim]{len(all_callers)} caller(s) found[/]")


@main.command()
@click.argument("symbol")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--depth", default=2, show_default=True, help="How many hops to follow.")
@click.option("--callers", "direction", flag_value="callers", help="Show what calls SYMBOL instead.")
@click.option("--callees", "direction", flag_value="callees", default=True, help="Show what SYMBOL calls (default).")
def trace(symbol: str, repo_path: str, depth: int, direction: str) -> None:
    """Trace the call chain from SYMBOL — understand a flow top-down or bottom-up.

    Default (--callees): follows what SYMBOL calls. Use to understand what
    happens when a function runs — the full execution path downward.

    With --callers: shows what calls SYMBOL upward. Similar to who-calls
    but as an indented tree instead of a flat table.

    \b
    DEPTH: hops DOWN (callees) or UP (callers) the call chain. Max 5.
      --depth 1: only the direct calls from SYMBOL.
      --depth 2 (default): calls, and what those call.
      --depth 3: three levels — recommended for understanding a full flow.
      --depth 5: maximum. Use for deep or unfamiliar codebases.
    Higher depth = more of the chain, but also more noise.

    \b
    WHO-CALLS vs TRACE --CALLERS:
      Same direction (upward), different output shape.
      who-calls → flat table, easy to scan for a list of callers.
      trace --callers → indented tree, shows the chain structure.

    \b
    EXAMPLES:
      orgraph trace apply_coupon .                   # what does it call?
      orgraph trace apply_coupon . --depth 4         # go deeper
      orgraph trace apply_coupon . --callers         # what calls it? (tree)
      orgraph trace Coupon.on_post . --depth 3       # full handler flow
    """
    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"
    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    depth = min(depth, 5)

    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.graph import query as gq

    with open_db_readonly(db_path) as db:
        roots = gq.resolve_symbol(db, symbol)
        if not roots:
            console.print(f"[red]Symbol '{symbol}' not found.[/]")
            raise SystemExit(1)

        root = roots[0]
        arrow = "▼ calls" if direction == "callees" else "▲ called by"
        console.print(f"\n[bold cyan]{arrow}[/] [bold yellow]{root['name']}[/]  [dim]{root['path']}:{root['line']}[/]\n")

        chain = gq.traverse_calls(db, root["uid"], direction, depth)

    if not chain:
        console.print("[yellow]No connections found.[/]")
        return

    repo_str = str(repo) + "/"
    for entry in chain:
        indent = "  " * entry["depth"]
        name = entry["to_symbol"] if direction == "callees" else entry["from_symbol"]
        path = entry["to_file"] if direction == "callees" else entry["from_file"]
        line = entry["to_line"] if direction == "callees" else entry["from_line"]
        rel_path = path.replace(repo_str, "") if path else ""
        kind_tag = f" [magenta][{entry['call_kind']}][/]" if entry.get("call_kind") and entry["call_kind"] != "local" else ""
        console.print(f"{indent}[cyan]{name}[/]{kind_tag}  [dim]{rel_path}:{line}[/]")

    console.print(f"\n[dim]{len(chain)} edge(s), depth={depth}[/]")


@main.command("file")
@click.argument("file_path")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
def file_symbols(file_path: str, repo_path: str) -> None:
    """List every function and class defined in a file, with line numbers.

    Use this as a table of contents before opening a file — you'll know
    exactly what's in it and which line to jump to.

    \b
    EXAMPLES:
      orgraph file controllers/DiscountController.py .
      orgraph file libs/OrderHelper.py .
    """
    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"
    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.graph import query as gq

    with open_db_readonly(db_path) as db:
        resolved = gq.resolve_file_path(db, file_path, repo)
        rows = gq.get_file_symbols(db, resolved) if resolved else []

    if not rows:
        console.print(f"[yellow]No symbols found for '{file_path}'. Check the path or re-run `orgraph index`.[/]")
        return

    display_path = rows[0]["path"] if rows else file_path
    repo_str = str(repo) + "/"
    console.print(f"\n[bold cyan]Symbols in[/] [green]{display_path.replace(repo_str, '')}[/]\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Line", justify="right", style="bold", width=6)
    table.add_column("Kind", style="dim", width=8)
    table.add_column("Name", style="cyan")

    for r in rows:
        kind_color = "yellow" if r["kind"] == "class" else "cyan"
        table.add_row(str(r["line"]), f"[{kind_color}]{r['kind']}[/]", r["name"])

    console.print(table)
    console.print(f"\n[dim]{len(rows)} symbol(s)[/]")


@main.command()
@click.argument("file_or_symbol")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
def context(file_or_symbol: str, repo_path: str) -> None:
    """Show the architectural position of a file or symbol before you edit it.

    Answers: "how central is this, and what moves with it?" Use this to
    understand blast radius before making a change.

    \b
    OUTPUT FIELDS:
      cluster         — group of files tightly coupled to this one. Changing
                        one often means changing the others.
      cluster files   — how many files are in that cluster.
      foundational    — yes means many things depend on it; change carefully.
      community       — Leiden community ID. Functions in the same community
                        tend to change together across commits.
      call depth      — 0 = entry point (nothing calls it from above).
                        Higher = deeper in the stack, called by many layers.
      indegree        — how many functions call this one directly.
                        High indegree = widely used = risky to change.
      cluster files   — other files in the same tight cluster.
      community peers — other functions that statistically move with this one
                        (not necessarily callers — just co-change frequently).

    \b
    EXAMPLES:
      orgraph context controllers/DiscountController.py .
      orgraph context get_valid_coupon .
      orgraph context libs/OrderHelper.py .
    """
    from pathlib import Path as P
    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.graph import query as gq
    from orgraph.topology.serialise import load_communities, load_topology

    repo = P(repo_path).resolve()
    orgraph_dir = _orgraph_dir(repo)
    db_path = orgraph_dir / "graph.kuzu"

    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    topology = load_topology(orgraph_dir)
    communities = load_communities(orgraph_dir)

    uid_to_community: dict[str, int] = {}
    if communities:
        for cid, nodes in communities.items():
            for u in nodes:
                uid_to_community[u] = cid

    cluster_by_id = {c.cluster_id: c for c in topology.clusters} if topology else {}

    with open_db_readonly(db_path) as db:
        file_path: str | None = None
        uid: str | None = None

        target = P(file_or_symbol)
        if "/" in file_or_symbol or "\\" in file_or_symbol or "." in target.name:
            candidate = target if target.is_absolute() else repo / file_or_symbol
            if candidate.exists():
                file_path = str(candidate.resolve())
            else:
                file_path = file_or_symbol
        else:
            rows = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.name = $name "
                "RETURN f.path AS path, f.uid AS uid LIMIT 1",
                {"name": file_or_symbol},
            )
            if not rows:
                rows = db.query_to_dicts(
                    "MATCH (c:Class) WHERE c.name = $name "
                    "RETURN c.path AS path, c.uid AS uid LIMIT 1",
                    {"name": file_or_symbol},
                )
            if rows:
                file_path = rows[0]["path"]
                uid = rows[0]["uid"]
            else:
                console.print(f"[red]Symbol or file '{file_or_symbol}' not found.[/]")
                raise SystemExit(1)

        if not topology or not file_path:
            console.print("[red]No topology data. Re-run `orgraph index`.[/]")
            raise SystemExit(1)

        cluster_id = topology.file_cluster_id.get(file_path)
        cluster = cluster_by_id.get(cluster_id) if cluster_id else None

        community_id: int | None = None
        if uid:
            community_id = uid_to_community.get(uid)
        if community_id is None:
            rows = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.path = $path RETURN f.uid AS uid LIMIT 20",
                {"path": file_path},
            )
            for row in rows:
                cid = uid_to_community.get(row["uid"])
                if cid is not None:
                    community_id = cid
                    break

        indegree = (
            gq.get_symbol_indegree(db, uid) if uid
            else topology.file_indegree.get(file_path, 0)
        )
        call_depth = topology.file_call_depth.get(file_path)

        repo_str = str(repo) + "/"
        rel = file_path.replace(repo_str, "")
        console.print(f"\n[bold cyan]context[/] [green]{rel}[/]\n")

        info = Table(show_header=False, box=None, padding=(0, 2))
        info.add_column("Key", style="dim")
        info.add_column("Value", style="bold")
        info.add_row("cluster", cluster_id or "—")
        info.add_row("cluster files", str(len(cluster.all_files)) if cluster else "—")
        info.add_row("foundational", "yes" if (cluster and cluster.is_foundational) else "no")
        info.add_row("community", str(community_id) if community_id is not None else "—")
        info.add_row("call depth", str(call_depth) if call_depth is not None else "—")
        info.add_row("indegree", str(indegree))
        console.print(info)

        if cluster:
            related = [f.replace(repo_str, "") for f in cluster.all_files[:10] if f != file_path]
            if related:
                console.print("\n[bold]Cluster files:[/]")
                for f in related:
                    console.print(f"  [dim]{f}[/]")

        if community_id is not None and communities:
            peers_raw: list[dict] = []
            for peer_uid in communities.get(community_id, []):
                if peer_uid == uid:
                    continue
                r = db.query_to_dicts(
                    "MATCH (s:Function) WHERE s.uid = $uid "
                    "RETURN s.name AS name, s.path AS path, s.line_number AS line LIMIT 1",
                    {"uid": peer_uid},
                ) or db.query_to_dicts(
                    "MATCH (s:Class) WHERE s.uid = $uid "
                    "RETURN s.name AS name, s.path AS path, s.line_number AS line LIMIT 1",
                    {"uid": peer_uid},
                )
                if r and r[0].get("path") != file_path:
                    peers_raw.append(r[0])
                if len(peers_raw) >= 8:
                    break
            if peers_raw:
                console.print("\n[bold]Community peers:[/]")
                for p in peers_raw:
                    console.print(
                        f"  [cyan]{p['name']}[/]  "
                        f"[dim]{p['path'].replace(repo_str, '')}:{p['line']}[/]"
                    )


@main.command()
@click.argument("file_path")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--direction", default="imports", show_default=True,
              type=click.Choice(["imports", "imported_by"]),
              help="imports: what this file depends on. imported_by: what depends on it.")
@click.option("--depth", default=1, show_default=True, help="How many hops to traverse (max 3).")
def deps(file_path: str, repo_path: str, direction: str, depth: int) -> None:
    """Show what a file imports, or what imports it — file-level dependencies.

    Operates on import statements, not function calls. This is about module
    structure, not runtime behaviour. Use before deleting or moving a file.

    \b
    DIRECTION:
      imports (default)  — what this file pulls in.
                           "What do I need to understand before reading this?"
      imported_by        — what files import this one.
                           "What breaks if I delete or rename something here?"

    \b
    DEPTH: import layers to follow. Max 3.
      --depth 1 (default): direct imports only.
                           DiscountController.py → MetaInitializers.py
      --depth 2:           imports of imports (transitive).
                           DiscountController.py → MetaInitializers.py → db.py
      --depth 3:           three layers deep — the full dependency web.
    Higher depth surfaces hidden coupling but adds noise. Start at 1.

    \b
    NOTE: only sees static `import` statements at the top of files.
    Dynamic imports (importlib, __import__) are invisible to this command.

    \b
    EXAMPLES:
      orgraph deps controllers/DiscountController.py .
      orgraph deps controllers/DiscountController.py . --direction imported_by
      orgraph deps libs/OrderHelper.py . --depth 2
    """
    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.graph import query as gq

    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"
    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    repo_str = str(repo) + "/"

    with open_db_readonly(db_path) as db:
        abs_path = gq.resolve_file_path(db, file_path, repo)
        if not abs_path:
            console.print(f"[red]File '{file_path}' not found in index.[/]")
            raise SystemExit(1)

        result = gq.get_dependencies(db, abs_path, direction, min(depth, 3))

    if not result:
        console.print(f"[yellow]No {'imports' if direction == 'imports' else 'dependents'} found.[/]")
        return

    arrow = "imports →" if direction == "imports" else "← imported by"
    rel = abs_path.replace(repo_str, "")
    console.print(f"\n[bold cyan]{arrow}[/] [green]{rel}[/]\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="green")
    table.add_column("Alias", style="dim")
    table.add_column("Transitive", justify="center", style="dim")

    for d in result:
        path = (d.get("path") or "").replace(repo_str, "")
        table.add_row(
            d.get("name") or "—",
            path or "—",
            d.get("alias") or "",
            "yes" if d.get("transitive") else "",
        )

    console.print(table)
    console.print(f"\n[dim]{len(result)} dep(s), direction={direction}, depth={depth}[/]")


@main.command("entry-points")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--kind", default="http", show_default=True,
              type=click.Choice(["http", "tasks", "all"]),
              help="Which entry points to show.")
def entry_points(repo_path: str, kind: str) -> None:
    """List all HTTP handlers and async tasks — the entry points into the system.

    Best first command on a new repo. Gives you the full API surface without
    needing to know anything about the codebase first.

    To follow what an entry point does, pass its symbol to `trace`:
      orgraph trace Coupon.on_post . --depth 3

    \b
    OPTIONS:
      --kind   http (default): HTTP handlers only.
               tasks: Celery async tasks only.
               all: HTTP + tasks together.

    \b
    EXAMPLES:
      orgraph entry-points .                   # all HTTP handlers
      orgraph entry-points . --kind tasks      # Celery async tasks only
      orgraph entry-points . --kind all        # HTTP + tasks together
    """
    from orgraph.graph.kuzu import open_db_readonly
    from orgraph.graph import query as gq
    from orgraph.topology.serialise import load_topology

    repo = Path(repo_path).resolve()
    orgraph_dir = _orgraph_dir(repo)
    db_path = orgraph_dir / "graph.kuzu"

    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    topology = load_topology(orgraph_dir)
    repo_str = str(repo) + "/"

    with open_db_readonly(db_path) as db:
        if kind in ("http", "all"):
            handlers = gq.get_http_handlers(db)
            if handlers:
                table = Table(title="HTTP Handlers", show_header=True)
                table.add_column("Method", style="bold yellow", width=6)
                table.add_column("Symbol", style="cyan")
                table.add_column("File", style="green")
                table.add_column("Line", justify="right", style="bold")
                for r in handlers:
                    cluster_id = topology.file_cluster_id.get(r["path"]) if topology else ""
                    rel = r["path"].replace(repo_str, "")
                    table.add_row(
                        r.get("http_method") or "—",
                        r["name"],
                        rel,
                        str(r.get("line") or 0),
                    )
                console.print(table)
            else:
                console.print("[yellow]No HTTP handlers found.[/]")

        if kind in ("tasks", "all"):
            tasks = gq.get_celery_dispatches(db)
            if tasks:
                table = Table(title="Async Tasks", show_header=True)
                table.add_column("Task", style="cyan")
                table.add_column("File", style="green")
                table.add_column("Dispatcher", style="dim")
                for r in tasks:
                    rel = (r.get("task_path") or "").replace(repo_str, "")
                    table.add_row(
                        r.get("task") or "—",
                        rel,
                        r.get("caller") or "—",
                    )
                console.print(table)
            else:
                console.print("[yellow]No async tasks found.[/]")


@main.command()
@click.argument("repo_path", default=None, required=False, type=click.Path(exists=False, file_okay=False))
def serve(repo_path: str | None) -> None:
    """Start the MCP server (stdio transport).

    REPO_PATH: path to the repo to serve. If omitted, starts in global mode —
    callers pass `repo` as an argument to each tool call.
    """
    from orgraph.mcp.server import start_server

    repo = Path(repo_path).resolve() if repo_path else None
    start_server(repo)


@main.command()
@click.argument("repo_path", default=".", type=click.Path(file_okay=False))
def install(repo_path: str) -> None:
    """Interactively configure orgraph MCP for installed coding agents.

    REPO_PATH is the repo to register (default: current directory).
    An absolute path is baked into the MCP entry so agents always serve
    the right repo regardless of their working directory.
    """
    from orgraph.installer.installer import run
    run("install", Path(repo_path).resolve())


@main.command()
@click.argument("repo_path", default=".", type=click.Path(file_okay=False))
def uninstall(repo_path: str) -> None:
    """Remove orgraph MCP configuration from coding agents."""
    from orgraph.installer.installer import run
    run("uninstall", Path(repo_path).resolve())
