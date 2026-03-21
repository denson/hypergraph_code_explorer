"""
Tier 4 — Semantic Search (Embedding Fallback)
==============================================
Wraps EmbeddingManager for optional embedding-based retrieval.
Lazy-loads embeddings from disk. If no embeddings exist, computes them
(requires sentence-transformers to be installed).
"""

from __future__ import annotations

from pathlib import Path

from ..graph.builder import HypergraphBuilder
from .plan import (
    FileSuggestion,
    GrepSuggestion,
    RetrievalPlan,
    SymbolRelation,
)


def semantic_search(
    query: str,
    builder: HypergraphBuilder,
    embeddings_path: str | Path | None = None,
    *,
    top_k: int = 20,
    alpha: float = 0.6,
) -> RetrievalPlan:
    """Tier 4: embedding-based fallback.

    Lazy-loads embeddings from disk. If no embeddings exist, computes them
    (requires sentence-transformers to be installed via `pip install hce[embed]`).

    Args:
        query: The query string.
        builder: The hypergraph builder.
        embeddings_path: Path to saved embeddings (.pkl). If None, computes fresh.
        top_k: Number of top similar nodes to return.
        alpha: Balance between precision and coverage.

    Returns:
        A RetrievalPlan with files and symbols from embedding similarity.
    """
    try:
        from ..graph.embeddings import EmbeddingManager
    except ImportError as e:
        plan = RetrievalPlan(query=query, tiers_used=[4])
        plan.structural_context = (
            "Tier 4 (semantic search) requires sentence-transformers. "
            "Install with: pip install hypergraph-code-explorer[embed]"
        )
        return plan

    # Load or create embeddings
    if embeddings_path and Path(embeddings_path).exists():
        embeddings = EmbeddingManager.load(embeddings_path)
    else:
        embeddings = EmbeddingManager()
        embeddings.embed_all_from_builder(builder)
        # Save if path provided
        if embeddings_path:
            embeddings.save(embeddings_path)

    # Find top-k similar nodes
    all_nodes = list(builder._node_to_edges.keys())
    if not all_nodes:
        return RetrievalPlan(query=query, tiers_used=[4])

    similar = embeddings.top_k_similar(query, all_nodes, k=top_k)

    plan = RetrievalPlan(query=query, tiers_used=[4])
    files_seen: dict[str, FileSuggestion] = {}

    for node, score in similar:
        if score < 0.1:
            continue

        # Get edges for this node to find files
        edges = builder.get_edges_for_node(node)
        for edge in edges:
            if edge.source_path and edge.source_path not in files_seen:
                files_seen[edge.source_path] = FileSuggestion(
                    path=edge.source_path,
                    symbols=[],
                    reason=f"semantic match (score: {score:.2f})",
                    priority=2,
                )
            if edge.source_path:
                if node not in files_seen[edge.source_path].symbols:
                    files_seen[edge.source_path].symbols.append(node)

        # Symbol relation
        plan.related_symbols.append(SymbolRelation(
            name=node,
            file="",
            relationship="semantic match",
            edge_type="",
        ))

        # Grep suggestion
        last_part = node.rsplit(".", 1)[-1]
        plan.grep_suggestions.append(GrepSuggestion(
            pattern=last_part,
            reason=f"semantic match for {node}",
        ))

    plan.primary_files = sorted(files_seen.values(), key=lambda f: f.priority)
    return plan
