"""
MCP Server
==========
FastMCP server exposing 6 tools for hypergraph-based code exploration.

Supports multiple codebases simultaneously via a session registry.
Each indexed codebase gets its own session, keyed by source path.

Tools:
  - hce_index — index a codebase directory (or load existing cache)
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

    # ── Session registry ──────────────────────────────────────────────
    # Maps resolved source path → loaded HypergraphSession.
    # Allows multiple codebases to stay loaded in a single server process.
    _sessions: dict[str, object] = {}
    _active_path: str | None = None  # most recently used source path

    def _register(source_path: str, session: object):
        """Add a session to the registry and make it active."""
        nonlocal _active_path
        _sessions[source_path] = session
        _active_path = source_path

    def _get_session(path: str | None = None):
        """
        Get a session by source path.

        - If path is given and matches a registered session, return it.
        - If path is given but not registered, try to load from .hce_cache.
        - If path is None, return the most recently used session.
        - Falls back to HCE_CACHE_DIR env var or cwd/.hce_cache.
        """
        nonlocal _active_path
        from .api import HypergraphSession

        # Explicit path requested
        if path:
            resolved = str(Path(path).resolve())
            if resolved in _sessions:
                _active_path = resolved
                return _sessions[resolved]
            # Try loading from cache
            cache = Path(resolved) / ".hce_cache"
            if cache.exists():
                session = HypergraphSession.load(str(cache))
                _register(resolved, session)
                return session
            return None

        # Use active session
        if _active_path and _active_path in _sessions:
            return _sessions[_active_path]

        # Bootstrap from environment or cwd
        env_cache = os.environ.get("HCE_CACHE_DIR")
        if env_cache and Path(env_cache).exists():
            session = HypergraphSession.load(env_cache)
            # Derive source path from cache dir (parent of .hce_cache)
            source = str(Path(env_cache).parent) if Path(env_cache).name == ".hce_cache" else env_cache
            _register(source, session)
            return session

        cwd_cache = Path.cwd() / ".hce_cache"
        if cwd_cache.exists():
            session = HypergraphSession.load(str(cwd_cache))
            _register(str(Path.cwd()), session)
            return session

        return HypergraphSession()

    def _list_repos() -> list[dict]:
        """Return info about all loaded repos."""
        result = []
        for src_path, session in _sessions.items():
            stats = session.stats() if hasattr(session, 'stats') else {}
            result.append({
                "path": src_path,
                "name": Path(src_path).name,
                "active": src_path == _active_path,
                "nodes": stats.get("num_nodes", 0),
                "edges": stats.get("num_edges", 0),
            })
        return result

    # ── Tools ─────────────────────────────────────────────────────────

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

        If the directory already contains a .hce_cache/, loads the existing
        index instead of re-indexing. Use force=True in the path (not yet
        supported) to re-index.

        Args:
            path: Path to the source directory to index (e.g. "./my-project/src")
            skip_summaries: Skip LLM-based summaries for zero-cost indexing (default True)
        """
        import json
        from .api import HypergraphSession

        source_dir = Path(path).resolve()
        if not source_dir.is_dir():
            return json.dumps({"error": f"Directory not found: {path}"})

        source_key = str(source_dir)
        cache_dir = source_dir / ".hce_cache"

        # If cache exists, load it instead of re-indexing
        if cache_dir.exists():
            session = HypergraphSession.load(str(cache_dir))
            _register(source_key, session)
            stats = session.stats()
            return json.dumps({
                "status": "loaded_from_cache",
                "message": (
                    f"Found existing HCE index for {source_dir.name}/. "
                    f"Loaded {stats.get('num_nodes', 0)} symbols and "
                    f"{stats.get('num_edges', 0)} relationships. "
                    f"Ready to query."
                ),
                "path": source_key,
                "cache_dir": str(cache_dir),
                **stats,
            }, indent=2)

        # No cache — index from scratch
        from .pipeline import HypergraphPipeline
        from .codemap import generate_codemap

        source_files = [
            f for f in source_dir.rglob("*")
            if f.is_file() and f.suffix in (
                ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
                ".java", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".rb", ".php",
            )
        ]
        msg = [f"Indexing {len(source_files)} source files in {source_dir.name}/..."]

        pipeline = HypergraphPipeline(
            verbose=True,
            skip_summaries=skip_summaries,
        )

        stats = pipeline.index_directory(str(source_dir))
        generate_codemap(pipeline.builder, cache_dir=pipeline._cache_dir)

        # Load the new index into the registry
        session = HypergraphSession.load(str(cache_dir))
        _register(source_key, session)

        msg.append(
            f"Done. Built hypergraph with {stats.get('num_nodes', 0)} symbols "
            f"and {stats.get('num_edges', 0)} relationships "
            f"({stats.get('files_indexed', 0)} files indexed)."
        )
        edge_types = stats.get("edge_type_counts", {})
        if edge_types:
            parts = [f"{v} {k.lower()}" for k, v in edge_types.items()]
            msg.append(f"Edge breakdown: {', '.join(parts)}.")

        return json.dumps({
            "status": "indexed",
            "message": " ".join(msg),
            "path": source_key,
            "cache_dir": str(cache_dir),
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
        import json as _json
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

        # Build a description of what we're doing
        traversals = []
        if calls:
            traversals.append("outgoing calls")
        if callers:
            traversals.append("callers")
        if inherits:
            traversals.append("inheritance")
        if imports:
            traversals.append("imports")
        desc = f"Looking up '{symbol}'"
        if traversals:
            desc += f" — traversing {', '.join(traversals)} (depth={depth})"
        if _active_path:
            desc += f" in {Path(_active_path).name}"

        plan = session.lookup(
            symbol, edge_types=edge_types, depth=depth, direction=direction,
        )

        result = _json.loads(format_json(plan))
        result["_query"] = desc
        return _json.dumps(result, indent=2)

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
        import json as _json
        from .retrieval.plan import format_json

        session = _get_session()
        plan = session.search(term, max_results=max_results)
        result = _json.loads(format_json(plan))
        repo_name = Path(_active_path).name if _active_path else "unknown"
        result["_query"] = f"Searching for '{term}' in {repo_name} (max {max_results} results)"
        return _json.dumps(result, indent=2)

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
        import json as _json
        from .retrieval.plan import format_json

        session = _get_session()
        plan = session.query(query, depth=depth)
        result = _json.loads(format_json(plan))
        repo_name = Path(_active_path).name if _active_path else "unknown"
        result["_query"] = f"Querying {repo_name}: '{query}' (depth={depth})"
        return _json.dumps(result, indent=2)

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
        num_modules = len(result.get("modules", []))
        num_symbols = len(result.get("key_symbols", []))
        repo_name = Path(_active_path).name if _active_path else "unknown"
        result["_query"] = (
            f"Overview of {repo_name}: {num_modules} modules, "
            f"top {num_symbols} symbols by structural centrality"
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hce_stats() -> str:
        """Get graph statistics: node count, edge count, type breakdown, hub nodes."""
        import json
        session = _get_session()
        result = session.stats()
        nodes = result.get("num_nodes", 0)
        edges = result.get("num_edges", 0)
        repo_name = Path(_active_path).name if _active_path else "unknown"
        result["_query"] = f"Stats for {repo_name}: {nodes} nodes, {edges} edges"
        # Include list of all loaded repos
        result["_loaded_repos"] = _list_repos()
        return json.dumps(result, indent=2)

    return mcp


def main():
    """Entry point for hce-server command."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
