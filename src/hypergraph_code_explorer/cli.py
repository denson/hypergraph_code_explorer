"""
CLI Interface
=============
Subcommands: index, lookup, search, query, overview, init, embed, stats, server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="hce",
        description="Hypergraph Code Explorer — structural code intelligence.\n\n"
        "HCE gives you the REVERSE call graph — information that is expensive or\n"
        "impossible to extract by reading code. Reading code shows you what a function\n"
        "calls (forward edges). HCE shows you what calls it (backward edges), the\n"
        "full transitive dependency chain, and which symbols are hubs.\n\n"
        "USE HCE WHEN YOU NEED TO:\n"
        "  - Find all callers of a symbol (blast radius / impact analysis)\n"
        "  - Trace reverse dependencies across multiple hops (--depth 2+)\n"
        "  - Find all subclasses or implementors of an interface\n"
        "  - Identify hub symbols that many other modules depend on\n"
        "  - Get a completeness guarantee (\"ALL callers\", not \"callers grep found\")\n\n"
        "DON'T USE HCE WHEN:\n"
        "  - Tracing a call chain forward (just read the code — it's right there)\n"
        "  - Searching for a string or pattern (use grep)\n"
        "  - Reading a specific file (use cat/read)\n\n"
        "TYPICAL WORKFLOW FOR IMPACT ANALYSIS:\n"
        "  1. hce lookup Symbol --callers --depth 2  (who depends on this?)\n"
        "  2. hce lookup Symbol --inherits            (what overrides this?)\n"
        "  3. hce overview --top 20                   (is this a hub node?)\n"
        "  4. Read the specific files identified to understand the dependency",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- index ----
    index_p = subparsers.add_parser("index", help="Index a codebase")
    index_p.add_argument("path", type=str, help="Path to directory to index")
    index_p.add_argument("--text-edges", action="store_true",
                         help="Enable LLM-based text edge extraction")
    index_p.add_argument("--skip-summaries", action="store_true",
                         help="Skip summary generation")
    index_p.add_argument("--embed", action="store_true",
                         help="Compute embeddings at index time (for Tier 4)")
    index_p.add_argument("--summary-model", choices=["haiku", "sonnet"],
                         default="haiku", help="Model for summaries")
    index_p.add_argument("--verbose", "-v", action="store_true")

    # ---- lookup ----
    lookup_p = subparsers.add_parser("lookup",
        help="Find a symbol's relationships — callers, callees, inheritance, imports")
    lookup_p.add_argument("symbol", type=str,
        help="Symbol name (e.g. 'Session.send', 'ValidationError', 'run_validators')")
    lookup_p.add_argument("--calls", action="store_true",
        help="What does this symbol call? (forward — usually visible from reading code)")
    lookup_p.add_argument("--callers", action="store_true",
        help="What calls this symbol? (reverse — the key thing HCE provides that grep can't)")
    lookup_p.add_argument("--inherits", action="store_true",
        help="Inheritance chain — subclasses, base classes, overrides")
    lookup_p.add_argument("--imports", action="store_true",
        help="Import relationships — who imports this module/symbol")
    lookup_p.add_argument("--raises", action="store_true",
        help="What exceptions does this symbol raise")
    lookup_p.add_argument("--depth", type=int, default=1,
        help="How many hops to traverse (default: 1). Use 2+ for transitive dependencies")
    lookup_p.add_argument("--json", action="store_true", dest="json_output",
                          help="Output as JSON")
    lookup_p.add_argument("--cache-dir", type=str, default=None)
    lookup_p.add_argument("--verbose", "-v", action="store_true")

    # ---- search ----
    search_p = subparsers.add_parser("search",
        help="Find symbols by name substring (e.g. 'clean', 'validate', 'error')")
    search_p.add_argument("term", type=str,
        help="Substring to match against symbol names, file paths, and relations")
    search_p.add_argument("--type", type=str, default=None,
                          help="Filter by edge type (CALLS, IMPORTS, etc.)")
    search_p.add_argument("--json", action="store_true", dest="json_output")
    search_p.add_argument("--cache-dir", type=str, default=None)
    search_p.add_argument("--verbose", "-v", action="store_true")

    # ---- query ----
    query_p = subparsers.add_parser("query",
        help="Ask a question in plain English (routes through lookup + search automatically)")
    query_p.add_argument("query", type=str,
        help="e.g. 'what depends on ValidationError' or 'how is run_validators called'")
    query_p.add_argument("--depth", type=int, default=2, help="Traversal depth")
    query_p.add_argument("--json", action="store_true", dest="json_output")
    query_p.add_argument("--cache-dir", type=str, default=None)
    query_p.add_argument("--verbose", "-v", action="store_true")

    # ---- overview ----
    overview_p = subparsers.add_parser("overview",
        help="Show most-connected symbols (hub nodes) and module list — start here for orientation")
    overview_p.add_argument("--top", type=int, default=10,
        help="How many hub symbols to show (default: 10). Hub nodes have the largest blast radius")
    overview_p.add_argument("--json", action="store_true", dest="json_output")
    overview_p.add_argument("--cache-dir", type=str, default=None)
    overview_p.add_argument("--verbose", "-v", action="store_true")

    # ---- init ----
    init_p = subparsers.add_parser("init", help="Generate tool instruction files")
    init_p.add_argument("--tool", type=str, default="all",
                        choices=["claude-code", "cursor", "codex", "all"],
                        help="Which tool to generate instructions for")
    init_p.add_argument("--cache-dir", type=str, default=None)

    # ---- embed ----
    embed_p = subparsers.add_parser("embed", help="Compute embeddings for Tier 4")
    embed_p.add_argument("--force", action="store_true",
                         help="Recompute even if embeddings exist")
    embed_p.add_argument("--cache-dir", type=str, default=None)
    embed_p.add_argument("--verbose", "-v", action="store_true")

    # ---- stats ----
    stats_p = subparsers.add_parser("stats",
        help="Graph size, edge type breakdown, number of hub nodes")
    stats_p.add_argument("--json", action="store_true", dest="json_output")
    stats_p.add_argument("--cache-dir", type=str, default=None)

    # ---- server ----
    subparsers.add_parser("server", help="Start MCP server")

    args = parser.parse_args()

    # Route to handler
    handlers = {
        "index": _run_index,
        "lookup": _run_lookup,
        "search": _run_search,
        "query": _run_query,
        "overview": _run_overview,
        "init": _run_init,
        "embed": _run_embed,
        "stats": _run_stats,
        "server": _run_server,
    }
    handlers[args.command](args)


# ---------------------------------------------------------------------------
# Helper: load builder from cache
# ---------------------------------------------------------------------------

def _load_builder(cache_dir: str | None):
    """Load a HypergraphBuilder from a cache directory.

    Searches up parent directories for .hce_cache if no cache_dir given.
    """
    from .graph.builder import HypergraphBuilder

    if cache_dir:
        path = Path(cache_dir) / "builder.pkl"
    else:
        # Search for .hce_cache in cwd and parent dirs
        search = Path.cwd()
        path = None
        for _ in range(5):
            candidate = search / ".hce_cache" / "builder.pkl"
            if candidate.exists():
                path = candidate
                break
            search = search.parent

        if path is None:
            print("Error: No cached index found. Run 'hce index <path>' first.",
                  file=sys.stderr)
            sys.exit(1)

    return HypergraphBuilder.load(path)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _run_index(args):
    from dotenv import load_dotenv
    load_dotenv()

    from .pipeline import HypergraphPipeline

    model_map = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
    }

    pipeline = HypergraphPipeline(
        verbose=args.verbose,
        text_edges=args.text_edges,
        skip_summaries=args.skip_summaries,
        summary_model=model_map[args.summary_model],
    )

    anthropic_client = None
    if args.text_edges or not args.skip_summaries:
        try:
            import anthropic
            anthropic_client = anthropic.Anthropic()
        except Exception as e:
            if not args.skip_summaries:
                print(f"Warning: Could not create Anthropic client: {e}")
                print("Summaries will be skipped.")
                pipeline.skip_summaries = True

    stats = pipeline.index_directory(
        args.path,
        anthropic_client=anthropic_client,
    )

    # Generate codemap after indexing
    from .codemap import generate_codemap
    generate_codemap(pipeline.builder, cache_dir=pipeline._cache_dir)

    # Optionally compute embeddings
    if args.embed:
        if args.verbose:
            print("  Computing embeddings...")
        from .graph.embeddings import EmbeddingManager
        embeddings = EmbeddingManager(verbose=args.verbose)
        embeddings.embed_all_from_builder(pipeline.builder)
        if pipeline._cache_dir:
            embeddings.save(pipeline._cache_dir / "embeddings.pkl")

    print("\n=== Index Complete ===")
    print(json.dumps(stats, indent=2))


def _run_lookup(args):
    builder = _load_builder(args.cache_dir)

    from .retrieval.lookup import lookup
    from .retrieval.traverse import traverse
    from .retrieval.plan import format_text, format_json

    # Determine edge types from flags
    edge_types: list[str] = []
    if args.calls:
        edge_types.append("CALLS")
    if args.callers:
        edge_types.append("CALLS")  # same type, different direction
    if args.inherits:
        edge_types.append("INHERITS")
    if args.imports:
        edge_types.append("IMPORTS")
    if args.raises:
        edge_types.append("RAISES")
    if not edge_types:
        edge_types = None  # show all

    # Determine direction
    direction = "both"
    if args.calls and not args.callers:
        direction = "forward"
    elif args.callers and not args.calls:
        direction = "backward"

    plan = lookup(args.symbol, builder, edge_types=edge_types)

    # If the lookup returned no actionable results (no files) but would have
    # results without the edge_type filter, this is likely a class node where
    # CALLS edges live on child methods. Expand by looking up the class without
    # the filter, finding DEFINES targets, then looking those up with the filter.
    if plan.is_empty() or not plan.primary_files:
        # Try unfiltered lookup to find the class definition
        unfiltered = lookup(args.symbol, builder, edge_types=None)
        if not unfiltered.is_empty():
            # Find child nodes via DEFINES edges
            child_nodes: list[str] = []
            for sym in unfiltered.related_symbols:
                if sym.relationship == "defines" and sym.targets:
                    child_nodes.extend(sym.targets)
            if child_nodes and edge_types:
                # Re-run lookup on child methods with the edge type filter
                for child in child_nodes[:20]:
                    child_plan = lookup(child, builder, edge_types=edge_types)
                    plan.merge(child_plan)
                # Also merge the unfiltered plan for context (class definition, inheritance)
                plan.merge(unfiltered)

    # If depth > 0, run Tier 2 traversal from the looked-up nodes
    if args.depth > 0 and not plan.is_empty():
        seed_nodes = list({s.name for s in plan.related_symbols})
        t2 = traverse(
            seed_nodes[:5], builder,
            edge_types=edge_types, depth=args.depth, direction=direction,
        )
        plan.merge(t2)

    if args.json_output:
        print(format_json(plan))
    else:
        print(format_text(plan))


def _run_search(args):
    builder = _load_builder(args.cache_dir)

    from .retrieval.textsearch import text_search
    from .retrieval.plan import format_text, format_json

    plan = text_search(args.term, builder)

    if args.json_output:
        print(format_json(plan))
    else:
        print(format_text(plan))


def _run_query(args):
    builder = _load_builder(args.cache_dir)

    from .retrieval.dispatch import dispatch
    from .retrieval.plan import format_text, format_json

    plan = dispatch(args.query, builder, depth=args.depth)

    if args.json_output:
        print(format_json(plan))
    else:
        print(format_text(plan))


def _run_overview(args):
    builder = _load_builder(args.cache_dir)

    from .retrieval.plan import Overview

    # Gather modules (unique source_paths)
    modules: list[dict] = []
    all_paths: set[str] = set()
    for rec in builder._edge_store.values():
        if rec.source_path:
            all_paths.add(rec.source_path)

    for path in sorted(all_paths):
        modules.append({"path": path})

    # Key symbols by degree
    key_symbols: list[dict] = []
    for node, edge_ids in sorted(
        builder._node_to_edges.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )[:args.top]:
        key_symbols.append({
            "name": node,
            "degree": len(edge_ids),
        })

    overview = Overview(
        modules=modules,
        key_symbols=key_symbols,
        reading_order=[],
    )

    if args.json_output:
        print(json.dumps(overview.to_dict(), indent=2))
    else:
        print("=== Modules ===")
        for m in modules:
            print(f"  {m['path']}")
        print()
        print(f"=== Key Symbols (top {args.top}) ===")
        for s in key_symbols:
            print(f"  {s['name']} (degree: {s['degree']})")


def _run_init(args):
    from .init import generate_init_file, generate_all_init_files

    if args.tool == "all":
        paths = generate_all_init_files()
        for p in paths:
            print(f"  Generated: {p}")
    else:
        path = generate_init_file(args.tool)
        print(f"  Generated: {path}")


def _run_embed(args):
    builder = _load_builder(args.cache_dir)

    from .graph.embeddings import EmbeddingManager

    # Determine cache dir for saving
    cache_dir = args.cache_dir
    if cache_dir is None:
        search = Path.cwd()
        for _ in range(5):
            candidate = search / ".hce_cache"
            if candidate.exists():
                cache_dir = str(candidate)
                break
            search = search.parent

    embeddings_path = Path(cache_dir) / "embeddings.pkl" if cache_dir else None

    if not args.force and embeddings_path and embeddings_path.exists():
        print("Embeddings already exist. Use --force to recompute.")
        return

    verbose = getattr(args, 'verbose', False)
    if verbose:
        print("Computing embeddings...")

    embeddings = EmbeddingManager(verbose=verbose)
    embeddings.embed_all_from_builder(builder)

    if embeddings_path:
        embeddings.save(embeddings_path)
        print(f"Embeddings saved to {embeddings_path}")
    else:
        print("Warning: No cache directory found. Embeddings not saved.")


def _run_stats(args):
    builder = _load_builder(args.cache_dir)

    stats = builder.stats()
    hub_nodes = builder.get_hub_nodes()
    stats["hub_nodes"] = len(hub_nodes)

    if args.json_output:
        print(json.dumps(stats, indent=2))
    else:
        print("=== Graph Statistics ===")
        for key, value in stats.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.2f}")
            elif isinstance(value, dict):
                print(f"  {key}:")
                for k, v in value.items():
                    print(f"    {k}: {v}")
            else:
                print(f"  {key}: {value}")


def _run_server(args):
    from .mcp_server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
