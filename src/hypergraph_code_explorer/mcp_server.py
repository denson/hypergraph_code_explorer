"""
MCP Server
==========
FastMCP server exposing 6 tools for hypergraph-based code exploration.

Tools:
  - hce_index — index a codebase directory into a hypergraph
  - hce_lookup — exact symbol lookup + structural traversal
  - hce_search — text search across all symbols
  - hce_query — full dispatch query through all tiers
  - hce_overview — codebase overview
  - hce_stats — graph statistics
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def create_server():
    """Create and configure the MCP server."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("hypergraph-code-explorer")

    # Session is loaded lazily; cache_dir tracks which index is loaded
    _session = None
    _loaded_cache_dir = None

    def _get_session(cache_dir: str | None = None):
        nonlocal _session, _loaded_cache_dir

        # If a specific cache_dir is requested and differs from current, reload
        if cache_dir:
            cache_path = Path(cache_dir)
            if cache_path.exists() and str(cache_path) != _loaded_cache_dir:
                from .api import HypergraphSession
                _session = HypergraphSession.load(cache_path)
                _loaded_cache_dir = str(cache_path)
                return _session

        if _session is None:
            from .api import HypergraphSession

            env_cache = os.environ.get("HCE_CACHE_DIR")
            if env_cache and Path(env_cache).exists():
                _session = HypergraphSession.load(env_cache)
                _loaded_cache_dir = env_cache
            else:
                cwd_cache = Path.cwd() / ".hce_cache"
                if cwd_cache.exists():
                    _session = HypergraphSession.load(cwd_cache)
                    _loaded_cache_dir = str(cwd_cache)
                else:
                    _session = HypergraphSession()
        return _session

    def _reload_session(cache_dir: str):
        """Force reload session from a cache directory."""
        nonlocal _session, _loaded_cache_dir
        from .api import HypergraphSession
        _session = HypergraphSession.load(cache_dir)
        _loaded_cache_dir = cache_dir

    @mcp.tool()
    def hce_index(
        path: str,
        skip_summaries: bool = True,
    ) -> str:
        """
        Index a codebase directory into a hypergraph. Point at the source root
        (the directory containing the actual source code, e.g. django/django/
        not django/). Creates a .hce_cache/ directory inside the source root.

        After indexing, all other HCE tools (lookup, search, query, overview,
        stats) will use this index automatically.

        Args:
            path: Path to the source directory to index (e.g. "./my-project/src")
            skip_summaries: Skip LLM-based summaries for zero-cost indexing (default True)
        """
        import json
        from .pipeline import HypergraphPipeline
        from .codemap import generate_codemap

        source_dir = Path(path).resolve()
        if not source_dir.is_dir():
            return json.dumps({"error": f"Directory not found: {path}"})

        pipeline = HypergraphPipeline(
            verbose=True,
            skip_summaries=skip_summaries,
        )

        stats = pipeline.index_directory(str(source_dir))
        generate_codemap(pipeline.builder, cache_dir=pipeline._cache_dir)

        # Reload the session with the new index
        cache_dir = str(source_dir / ".hce_cache")
        _reload_session(cache_dir)

        return json.dumps({
            "status": "indexed",
            "path": str(source_dir),
            "cache_dir": cache_dir,
            **stats,
        }, indent=2)

    @mcp.tool()
    def hce_lookup(
        symbol: str,
        calls: bool = False,
        callers: bool = False,
        inherits: bool = False,
        imports: bool = False,
        depth: int = 1,
    ) -> str:
        """
        Look up a symbol in the code graph. Returns file paths, related
        symbols, and grep patterns for the matched symbol.

        Args:
            symbol: Symbol name to look up (e.g. "Session.send")
            calls: Show what this symbol calls
            callers: Show what calls this symbol
            inherits: Show inheritance relationships
            imports: Show import relationships
            depth: Traversal depth for structural expansion (default 1)
        """
        from .retrieval.plan import format_json

        session = _get_session()

        # Determine edge types from flags
        edge_types: list[str] = []
        if calls:
            edge_types.append("CALLS")
        if callers:
            edge_types.append("CALLS")
        if inherits:
            edge_types.append("INHERITS")
        if imports:
            edge_types.append("IMPORTS")
        if not edge_types:
            edge_types = None

        # Determine direction
        direction = "both"
        if calls and not callers:
            direction = "forward"
        elif callers and not calls:
            direction = "backward"

        plan = session.lookup(
            symbol, edge_types=edge_types, depth=depth, direction=direction,
        )
        return format_json(plan)

    @mcp.tool()
    def hce_search(
        term: str,
        max_results: int = 20,
    ) -> str:
        """
        Text search across all symbols in the code graph. Finds symbols
        by substring matching on names, file paths, and relations.

        Args:
            term: Search term (e.g. "auth", "send")
            max_results: Maximum number of results (default 20)
        """
        from .retrieval.plan import format_json

        session = _get_session()
        plan = session.search(term, max_results=max_results)
        return format_json(plan)

    @mcp.tool()
    def hce_query(
        query: str,
        depth: int = 2,
    ) -> str:
        """
        Natural language query against the code graph. Routes through
        multiple retrieval tiers: exact lookup, structural traversal,
        and text search.

        Args:
            query: Natural language question about the codebase
            depth: Traversal depth for structural expansion (default 2)
        """
        from .retrieval.plan import format_json

        session = _get_session()
        plan = session.query(query, depth=depth)
        return format_json(plan)

    @mcp.tool()
    def hce_overview(
        top: int = 10,
    ) -> str:
        """
        Get a codebase overview: modules, key symbols by connectivity,
        and reading order.

        Args:
            top: Number of top symbols to include (default 10)
        """
        import json
        session = _get_session()
        result = session.overview(top=top)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hce_stats() -> str:
        """Get graph statistics: node count, edge count, type breakdown, hub nodes."""
        import json
        session = _get_session()
        result = session.stats()
        return json.dumps(result, indent=2)

    return mcp


def main():
    """Entry point for hce-server command."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
