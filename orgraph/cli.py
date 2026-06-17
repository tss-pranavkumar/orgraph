"""orgraph CLI — index / serve / search / status / eval."""
from __future__ import annotations

import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _orgraph_dir(repo_path: Path) -> Path:
    return repo_path / ".orgraph"


@contextmanager
def _open_db_readonly(db_path: Path):
    """Open a Kuzu DB for read-only queries, even if another process holds the lock.

    Kuzu acquires an exclusive lock even for read_only=True, so we copy the DB
    directory to a temp location and open that instead.
    """
    from orgraph.graph.kuzu import OrgraphDB

    tmp = tempfile.mkdtemp(prefix="orgraph_cli_")
    tmp_db = Path(tmp) / "graph.kuzu"
    try:
        shutil.copytree(str(db_path), str(tmp_db))
        db = OrgraphDB(tmp_db)
        try:
            yield db
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    from orgraph.extract.scip import ScipExtractor
    from orgraph.extract.treesitter import TreeSitterExtractor
    from orgraph.graph.builder import GraphBuilder
    from orgraph.graph.kuzu import OrgraphDB
    from orgraph.graph.schema import create_schema
    from orgraph.search.index import SearchIndex
    from orgraph.topology.cluster import build_nx_graph_from_result, cluster
    from orgraph.topology.context import build_repo_context
    from orgraph.topology.serialise import save_communities, save_topology
    from orgraph.topology.topology import build_topology_map

    repo = Path(repo_path).resolve()
    orgraph_dir = _orgraph_dir(repo)
    orgraph_dir.mkdir(parents=True, exist_ok=True)

    manifest = Manifest(orgraph_dir)
    if not force:
        manifest.load()

    t0 = time.perf_counter()

    # --- Extraction ---
    console.print(f"[bold cyan]orgraph[/] indexing [yellow]{repo}[/]")

    result = None

    scratch = orgraph_dir / "scip_scratch"
    scip = ScipExtractor(repo_path=repo, scratch_dir=scratch)
    with console.status("Trying SCIP extraction…"):
        result = scip.run()

    if result is not None:
        console.print(f"  [green]✓[/] SCIP extraction: {result.node_count()} nodes, {result.edge_count()} edges")
    else:
        console.print("  [dim]SCIP not available — falling back to tree-sitter[/]")
        with console.status("tree-sitter extraction…"):
            ts = TreeSitterExtractor(repo_path=repo)
            result = ts.run()
        console.print(f"  [green]✓[/] tree-sitter extraction: {result.node_count()} nodes, {result.edge_count()} edges")

    # --- Graph storage ---
    db_path = orgraph_dir / "graph.kuzu"
    with console.status("Writing to Kuzu…"):
        db = OrgraphDB(db_path)
        create_schema(db)
        builder = GraphBuilder(db=db, repo_path=repo)
        nodes_written, edges_written = builder.ingest(result)
        db.close()

    # --- Topology ---
    with console.status("Building topology clusters…"):
        ctx = build_repo_context(result, repo)
        topology = build_topology_map(ctx)

    non_foundational = [c for c in topology.clusters if not c.is_foundational]
    console.print(
        f"  [green]✓[/] Topology: [bold]{len(non_foundational)}[/] clusters"
        f" + {'1 foundational' if topology.foundational_files else '0 foundational'}"
    )

    # --- Leiden community detection ---
    with console.status("Running Leiden community detection…"):
        G = build_nx_graph_from_result(result)
        communities = cluster(G)

    console.print(f"  [green]✓[/] Communities: [bold]{len(communities)}[/] (Leiden/Louvain)")

    # --- Persist topology + communities ---
    save_topology(topology, orgraph_dir)
    save_communities(communities, orgraph_dir)

    # --- Search index ---
    with console.status("Building semble search index…"):
        SearchIndex.build(repo)
    console.print("  [green]✓[/] Search index built (.orgraph/search/)")

    # --- Manifest ---
    manifest.update(manifest.all_files(repo))
    manifest.save()

    elapsed = time.perf_counter() - t0
    console.print(
        f"\n[bold green]Done.[/] Indexed [bold]{nodes_written}[/] nodes, "
        f"[bold]{edges_written}[/] edges in [bold]{elapsed:.1f}s[/]"
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

    with _open_db_readonly(db_path) as db:

        # --- Node/edge counts ---
        labels = ["Function", "Class", "File", "Module", "Interface", "Struct", "Enum", "Variable"]
        table = Table(title=f"orgraph status — {repo.name}", show_header=True)
        table.add_column("Label", style="cyan")
        table.add_column("Count", justify="right", style="bold")

        total_nodes = 0
        for label in labels:
            try:
                rows = db.query_to_dicts(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                cnt = rows[0]["cnt"] if rows else 0
            except Exception:
                cnt = 0
            if cnt > 0:
                table.add_row(label, str(cnt))
                total_nodes += cnt

        table.add_row("─" * 12, "─" * 6)
        table.add_row("Total nodes", str(total_nodes))

        edge_labels = ["CALLS", "IMPORTS", "INHERITS", "CONTAINS", "IMPLEMENTS"]
        total_edges = 0
        for rel in edge_labels:
            try:
                rows = db.query_to_dicts(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS cnt")
                cnt = rows[0]["cnt"] if rows else 0
            except Exception:
                cnt = 0
            if cnt > 0:
                table.add_row(f"  [{rel}]", str(cnt))
                total_edges += cnt

        table.add_row("Total edges", str(total_edges))
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
    """Hybrid BM25+semantic search over a repo's code."""
    from orgraph.search.index import SearchIndex

    repo = Path(repo_path).resolve()
    idx = SearchIndex.load(repo)
    if idx is None:
        console.print("[red]Search index not built yet. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    results = idx.search(query, top_k=top_k)
    if not results:
        console.print("[yellow]No results.[/]")
        return

    for i, r in enumerate(results, 1):
        chunk = r.chunk
        console.print(
            f"[bold cyan]{i}.[/] score=[yellow]{r.score:.3f}[/]  "
            f"[green]{chunk.file_path}[/]:[bold]{chunk.start_line}-{chunk.end_line}[/]"
        )
        snippet = chunk.content[:200].replace("\n", " ").strip()
        console.print(f"   [dim]{snippet}[/]\n")


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
    """Show every place SYMBOL is called, with file and line number."""
    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"
    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    with _open_db_readonly(db_path) as db:
        rows = db.query_to_dicts(
            "MATCH (f:Function) WHERE f.name = $name RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 5",
            {"name": symbol},
        )
        if not rows:
            rows = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.name CONTAINS $name RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 5",
                {"name": symbol},
            )
        if not rows:
            console.print(f"[red]Symbol '{symbol}' not found in index.[/]")
            raise SystemExit(1)

        target = rows[0]
        console.print(f"\n[bold cyan]Who calls[/] [bold yellow]{target['name']}[/]  [dim]{target['path']}:{target['line']}[/]\n")

        visited: set[str] = {target["uid"]}
        frontier = [(target["uid"], target["name"], 0)]
        all_callers: list[dict] = []

        while frontier:
            uid, name, d = frontier.pop(0)
            if d >= depth:
                continue
            callers = db.query_to_dicts(
                "MATCH (c)-[r:CALLS]->(f) WHERE f.uid = $uid "
                "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line, r.line_number AS call_line LIMIT 50",
                {"uid": uid},
            )
            for c in callers:
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
    """Trace the call chain from SYMBOL — what it calls (or what calls it)."""
    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"
    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    depth = min(depth, 5)

    with _open_db_readonly(db_path) as db:
        roots = db.query_to_dicts(
            "MATCH (f:Function) WHERE f.name = $name RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 5",
            {"name": symbol},
        )
        if not roots:
            roots = db.query_to_dicts(
                "MATCH (f:Function) WHERE f.name CONTAINS $name RETURN f.uid AS uid, f.name AS name, f.path AS path, f.line_number AS line LIMIT 5",
                {"name": symbol},
            )
        if not roots:
            console.print(f"[red]Symbol '{symbol}' not found.[/]")
            raise SystemExit(1)

        root = roots[0]
        arrow = "▼ calls" if direction == "callees" else "▲ called by"
        console.print(f"\n[bold cyan]{arrow}[/] [bold yellow]{root['name']}[/]  [dim]{root['path']}:{root['line']}[/]\n")

        visited: set[str] = {root["uid"]}
        frontier = [(root["uid"], root["name"], 0)]
        chain: list[dict] = []

        while frontier:
            uid, name, d = frontier.pop(0)
            if d >= depth:
                continue
            if direction == "callees":
                q = ("MATCH (f)-[r:CALLS]->(c) WHERE f.uid = $uid "
                     "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line, r.call_kind AS call_kind LIMIT 30")
                q_fallback = ("MATCH (f)-[r:CALLS]->(c) WHERE f.uid = $uid "
                              "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line LIMIT 30")
            else:
                q = ("MATCH (c)-[r:CALLS]->(f) WHERE f.uid = $uid "
                     "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line, r.call_kind AS call_kind LIMIT 30")
                q_fallback = ("MATCH (c)-[r:CALLS]->(f) WHERE f.uid = $uid "
                              "RETURN c.uid AS uid, c.name AS name, c.path AS path, c.line_number AS line LIMIT 30")
            try:
                edges = db.query_to_dicts(q, {"uid": uid})
            except Exception:
                edges = db.query_to_dicts(q_fallback, {"uid": uid})
            for e in edges:
                chain.append({**e, "from": name, "depth": d + 1})
                if e["uid"] not in visited:
                    visited.add(e["uid"])
                    frontier.append((e["uid"], e["name"], d + 1))

    if not chain:
        console.print("[yellow]No connections found.[/]")
        return

    repo_str = str(repo) + "/"
    for entry in chain:
        indent = "  " * entry["depth"]
        rel_path = entry["path"].replace(repo_str, "") if entry["path"] else ""
        kind_tag = f" [magenta][{entry['call_kind']}][/]" if entry.get("call_kind") and entry["call_kind"] != "local" else ""
        console.print(f"{indent}[cyan]{entry['name']}[/]{kind_tag}  [dim]{rel_path}:{entry['line']}[/]")

    console.print(f"\n[dim]{len(chain)} edge(s), depth={depth}[/]")


@main.command("file")
@click.argument("file_path")
@click.argument("repo_path", default=".", type=click.Path(exists=True, file_okay=False))
def file_symbols(file_path: str, repo_path: str) -> None:
    """List all functions and classes defined in FILE_PATH."""
    repo = Path(repo_path).resolve()
    db_path = _orgraph_dir(repo) / "graph.kuzu"
    if not db_path.exists():
        console.print("[red]Not indexed. Run `orgraph index` first.[/]")
        raise SystemExit(1)

    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = repo / file_path
    abs_path = str(candidate.resolve()) if candidate.exists() else ""

    rows: list[dict] = []
    with _open_db_readonly(db_path) as db:
        for label, kind in (("Function", "function"), ("Class", "class")):
            if abs_path:
                part = db.query_to_dicts(
                    f"MATCH (s:{label}) WHERE s.path = $path RETURN s.name AS name, s.line_number AS line",
                    {"path": abs_path},
                )
            else:
                part = db.query_to_dicts(
                    f"MATCH (s:{label}) WHERE s.path CONTAINS $frag RETURN s.name AS name, s.line_number AS line LIMIT 100",
                    {"frag": file_path},
                )
            for r in part:
                rows.append({"name": r["name"], "line": r["line"] or 0, "kind": kind})

    if not rows:
        console.print(f"[yellow]No symbols found for '{file_path}'. Check the path or re-run `orgraph index`.[/]")
        return

    rows.sort(key=lambda r: r["line"])
    display_path = abs_path or file_path
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
