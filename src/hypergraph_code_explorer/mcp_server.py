"""
MCP Server
==========
FastMCP server exposing 8 tools for hypergraph-based code exploration.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def create_server():
    """Create and configure the MCP server."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("hypergraph-code-explorer")

    # Session is loaded lazily
    _session = None

    def _get_session():
        nonlocal _session
        if _session is None:
            from .api import HypergraphSession

            cache_dir = os.environ.get("HCE_CACHE_DIR")
            if cache_dir and Path(cache_dir).exists():
                _session = HypergraphSession.load(cache_dir)
            else:
                # Look for .hce_cache in current directory
                cwd_cache = Path.cwd() / ".hce_cache"
                if cwd_cache.exists():
                    _session = HypergraphSession.load(cwd_cache)
                else:
                    _session = HypergraphSession()
        return _session

    def _get_anthropic_client():
        import anthropic
        return anthropic.Anthropic()

    @mcp.tool()
    def hypergraph_retrieve(
        query: str,
        top_k: int = 20,
        alpha: float = 0.6,
    ) -> str:
        """
        Main retrieval: find relevant code relationships for a query.
        Returns edges with traversal paths showing how concepts connect
        through shared entities (intersection nodes).

        Args:
            query: Natural language query about the codebase
            top_k: Number of seed nodes to match (default 20)
            alpha: Balance precision vs coverage, 0-1 (default 0.6)
        """
        session = _get_session()
        result = session.retrieve(query=query, top_k=top_k, alpha=alpha)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hypergraph_find_path(
        source: str,
        target: str,
        k_paths: int = 3,
    ) -> str:
        """
        Find paths between two code entities through hyperedge space.
        Returns edge-level paths with intersection nodes explaining
        WHY each connection exists.

        Args:
            source: Source entity name (e.g. "Session")
            target: Target entity name (e.g. "HTTPAdapter")
            k_paths: Max number of paths to return (default 3)
        """
        session = _get_session()
        result = session.find_path(source=source, target=target, k_paths=k_paths)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hypergraph_neighbors(
        node: str,
        s: int = 1,
    ) -> str:
        """
        Get the edge-intersection neighbourhood of a node.
        Shows all edges incident on the node and edges that intersect them,
        grouped by the shared nodes that connect them.

        Args:
            node: Entity name to explore
            s: Minimum shared nodes for intersection (default 1)
        """
        session = _get_session()
        result = session.neighbors(node=node, s=s)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hypergraph_coverage(
        retrieved_edge_ids: list[str],
        seed_node_ids: list[str],
        depth: int = 1,
    ) -> str:
        """
        Evaluate coverage of previous retrieval results.
        No LLM calls — purely graph-structural analysis.
        Use when coverage_score < 0.5 or frontier nodes have high degree.

        Args:
            retrieved_edge_ids: Edge IDs from a previous retrieve call
            seed_node_ids: Matched node names from retrieve
            depth: Frontier expansion depth (default 1)
        """
        session = _get_session()
        result = session.coverage(
            retrieved_edge_ids=retrieved_edge_ids,
            seed_node_ids=seed_node_ids,
            depth=depth,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hypergraph_summarize(
        scope: str = "file",
        paths: list[str] | None = None,
        force: bool = False,
        model: str = "haiku",
    ) -> str:
        """
        Generate module-level summaries. Build-time operation.
        Creates SUMMARY edges that provide high-level orientation
        for broad queries.

        Args:
            scope: "file" or "directory" (default "file")
            paths: Specific files to summarize (None = all)
            force: Regenerate existing summaries (default False)
            model: "haiku" (fast/cheap) or "sonnet" (higher quality)
        """
        model_map = {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": "claude-sonnet-4-6",
        }
        model_id = model_map.get(model, model)

        session = _get_session()
        client = _get_anthropic_client()
        result = session.summarize(
            anthropic_client=client,
            scope=scope,
            paths=paths,
            force=force,
            model=model_id,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hypergraph_stats() -> str:
        """Get graph statistics: node count, edge count, type breakdown."""
        session = _get_session()
        result = session.stats()
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hypergraph_list_nodes(limit: int = 100) -> str:
        """
        List all nodes with degree info, sorted by degree descending.

        Args:
            limit: Max nodes to return (default 100)
        """
        session = _get_session()
        result = session.list_nodes(limit=limit)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hypergraph_list_edges(
        limit: int = 100,
        edge_type: str | None = None,
    ) -> str:
        """
        List edges with metadata, optionally filtered by type.

        Args:
            limit: Max edges to return (default 100)
            edge_type: Filter by type (CALLS, IMPORTS, DEFINES, etc.)
        """
        session = _get_session()
        result = session.list_edges(limit=limit, edge_type=edge_type)
        return json.dumps(result, indent=2)

    return mcp


def main():
    """Entry point for hce-server command."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
