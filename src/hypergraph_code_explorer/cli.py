"""
CLI Interface
=============
argparse with subcommands: index, query, server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="hce",
        description="Hypergraph Code Explorer — hypergraph-based code understanding",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- index ----
    index_parser = subparsers.add_parser("index", help="Index a codebase")
    index_parser.add_argument("path", type=str, help="Path to directory to index")
    index_parser.add_argument("--text-edges", action="store_true",
                              help="Enable LLM-based text edge extraction (opt-in)")
    index_parser.add_argument("--skip-summaries", action="store_true",
                              help="Skip summary generation")
    index_parser.add_argument("--summary-model", choices=["haiku", "sonnet"],
                              default="haiku", help="Model for summaries (default: haiku)")
    index_parser.add_argument("--verbose", "-v", action="store_true")

    # ---- query ----
    query_parser = subparsers.add_parser("query", help="Query the indexed codebase")
    query_parser.add_argument("query", type=str, help="Natural language query")
    query_parser.add_argument("--top-k", type=int, default=20, help="Top-K nodes (default: 20)")
    query_parser.add_argument("--alpha", type=float, default=0.6, help="Alpha parameter (default: 0.6)")
    query_parser.add_argument("--cache-dir", type=str, default=None,
                              help="Path to cached index (default: .hce_cache in query path)")
    query_parser.add_argument("--verbose", "-v", action="store_true")

    # ---- server ----
    server_parser = subparsers.add_parser("server", help="Start MCP server")

    args = parser.parse_args()

    if args.command == "index":
        _run_index(args)
    elif args.command == "query":
        _run_query(args)
    elif args.command == "server":
        _run_server()


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

    print("\n=== Index Complete ===")
    print(json.dumps(stats, indent=2))


def _run_query(args):
    from .pipeline import HypergraphPipeline

    cache_dir = args.cache_dir
    if cache_dir is None:
        # Try to find .hce_cache in cwd
        cwd_cache = Path.cwd() / ".hce_cache"
        if cwd_cache.exists():
            cache_dir = str(cwd_cache)
        else:
            print("Error: No cached index found. Run 'hce index <path>' first.", file=sys.stderr)
            sys.exit(1)

    pipeline = HypergraphPipeline(verbose=args.verbose)
    pipeline.load(cache_dir)

    result = pipeline.query(
        query=args.query,
        top_k=args.top_k,
        alpha=args.alpha,
    )

    # Print context text for human consumption
    print(result.get("context_text", ""))


def _run_server():
    from .mcp_server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
