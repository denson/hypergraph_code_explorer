"""
API Layer
=========
HypergraphSession — serializable API layer between pipeline and MCP server.
Wraps pipeline operations into methods matching the 8 MCP tools.
"""

from __future__ import annotations

from pathlib import Path

from .pipeline import HypergraphPipeline


class HypergraphSession:
    """Wraps a HypergraphPipeline for use by the MCP server."""

    def __init__(self, pipeline: HypergraphPipeline | None = None):
        self._pipeline = pipeline or HypergraphPipeline()

    @classmethod
    def load(cls, path: str | Path) -> HypergraphSession:
        """Load a session from a saved pipeline state."""
        pipeline = HypergraphPipeline()
        pipeline.load(path)
        return cls(pipeline)

    @classmethod
    def create_and_index(
        cls,
        directory: str | Path,
        verbose: bool = False,
        text_edges: bool = False,
        skip_summaries: bool = False,
        summary_model: str = "claude-haiku-4-5-20251001",
        anthropic_client=None,
    ) -> HypergraphSession:
        """Create a new session and index a directory."""
        pipeline = HypergraphPipeline(
            verbose=verbose,
            text_edges=text_edges,
            skip_summaries=skip_summaries,
            summary_model=summary_model,
        )
        pipeline.index_directory(directory, anthropic_client=anthropic_client)
        return cls(pipeline)

    # ---- MCP tool methods --------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        alpha: float = 0.6,
    ) -> dict:
        """hypergraph_retrieve — main retrieval with traversal paths."""
        return self._pipeline.query(query=query, top_k=top_k, alpha=alpha)

    def find_path(
        self,
        source: str,
        target: str,
        k_paths: int = 3,
    ) -> dict:
        """hypergraph_find_path — edge-BFS between two entities."""
        return self._pipeline.find_path(source=source, target=target, k_paths=k_paths)

    def neighbors(self, node: str, s: int = 1) -> dict:
        """hypergraph_neighbors — edge-intersection neighbourhood expansion."""
        return self._pipeline.get_neighbors(node=node, s=s)

    def coverage(
        self,
        retrieved_edge_ids: list[str],
        seed_node_ids: list[str],
        depth: int = 1,
    ) -> dict:
        """hypergraph_coverage — agent self-evaluation (no LLM)."""
        return self._pipeline.get_coverage(
            retrieved_edge_ids=retrieved_edge_ids,
            seed_node_ids=seed_node_ids,
            depth=depth,
        )

    def summarize(
        self,
        anthropic_client,
        scope: str = "file",
        paths: list[str] | None = None,
        force: bool = False,
        model: str = "claude-haiku-4-5-20251001",
    ) -> dict:
        """hypergraph_summarize — trigger summary generation."""
        from .graph.summaries import generate_summaries

        results = generate_summaries(
            builder=self._pipeline.builder,
            anthropic_client=anthropic_client,
            model=model,
            paths=paths,
            force=force,
            verbose=self._pipeline.verbose,
        )

        # Re-embed after new summaries
        self._pipeline.embeddings.embed_all_from_builder(self._pipeline.builder)

        return {
            "summaries_generated": len(results),
            "summary_edges_created": results,
        }

    def stats(self) -> dict:
        """hypergraph_stats — graph statistics."""
        return self._pipeline.stats()

    def list_nodes(self, limit: int = 100) -> list[dict]:
        """hypergraph_list_nodes — list all nodes with degree info."""
        return self._pipeline.list_nodes(limit=limit)

    def list_edges(
        self, limit: int = 100, edge_type: str | None = None
    ) -> list[dict]:
        """hypergraph_list_edges — list all edges with metadata."""
        return self._pipeline.list_edges(limit=limit, edge_type=edge_type)

    def save(self, path: str | Path) -> None:
        self._pipeline.save(path)
