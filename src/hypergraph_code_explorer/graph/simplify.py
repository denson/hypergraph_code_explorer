"""
Node Simplification
===================
After building the graph, merge nodes with cosine similarity > threshold.
Merge lower-degree into higher-degree. Update incidence dict, edge store,
inverted index, and embeddings in one pass.

Default threshold: 0.97 (higher than MIT's 0.90; code identifiers are precise).
"""

from __future__ import annotations

import numpy as np

from .builder import HypergraphBuilder
from .embeddings import EmbeddingManager


def simplify_graph(
    builder: HypergraphBuilder,
    embeddings: EmbeddingManager,
    threshold: float = 0.97,
    verbose: bool = False,
) -> dict[str, str]:
    """
    Merge near-duplicate nodes in the hypergraph.

    Args:
        builder: The hypergraph builder (modified in place)
        embeddings: The embedding manager (modified in place)
        threshold: Cosine similarity threshold for merging
        verbose: Print progress

    Returns:
        Dict mapping merged node names to their keeper names
    """
    all_nodes = sorted(builder.get_all_nodes())
    if len(all_nodes) < 2:
        return {}

    # Filter to nodes that have embeddings
    embedded_nodes = [n for n in all_nodes if n in embeddings]
    if len(embedded_nodes) < 2:
        return {}

    # Build similarity matrix
    vecs = np.stack([embeddings.get(n) for n in embedded_nodes])
    sim_matrix = vecs @ vecs.T  # already normalised

    # Find merge pairs from upper triangle
    merge_pairs: list[tuple[str, str, float]] = []
    for i in range(len(embedded_nodes)):
        for j in range(i + 1, len(embedded_nodes)):
            if sim_matrix[i, j] >= threshold:
                merge_pairs.append((embedded_nodes[i], embedded_nodes[j], float(sim_matrix[i, j])))

    if not merge_pairs:
        return {}

    if verbose:
        print(f"  Found {len(merge_pairs)} node pairs above {threshold} similarity")

    # Decide keeper vs merged: keep the higher-degree node
    merge_map: dict[str, str] = {}  # merged → keeper
    for node_a, node_b, sim in sorted(merge_pairs, key=lambda x: -x[2]):
        # Resolve transitively
        a = _resolve(node_a, merge_map)
        b = _resolve(node_b, merge_map)
        if a == b:
            continue

        deg_a = builder.get_node_degree(a)
        deg_b = builder.get_node_degree(b)

        if deg_a >= deg_b:
            keeper, merged = a, b
        else:
            keeper, merged = b, a

        merge_map[merged] = keeper

    if verbose:
        print(f"  Merging {len(merge_map)} nodes")

    # Apply merges to builder
    for merged, keeper in merge_map.items():
        _merge_node(builder, merged, keeper)
        # Update embeddings: keep the keeper, remove the merged
        embeddings.remove(merged)

    return merge_map


def _resolve(node: str, merge_map: dict[str, str]) -> str:
    """Follow the merge chain to find the final keeper."""
    visited: set[str] = set()
    while node in merge_map:
        if node in visited:
            break
        visited.add(node)
        node = merge_map[node]
    return node


def _merge_node(builder: HypergraphBuilder, merged: str, keeper: str) -> None:
    """Replace all occurrences of `merged` with `keeper` in the builder."""
    edge_ids = list(builder._node_to_edges.get(merged, set()))

    for eid in edge_ids:
        # Update incidence
        if eid in builder._incidence:
            builder._incidence[eid].discard(merged)
            builder._incidence[eid].add(keeper)

        # Update edge store
        record = builder._edge_store.get(eid)
        if record:
            record.all_nodes.discard(merged)
            record.all_nodes.add(keeper)
            record.sources = [keeper if s == merged else s for s in record.sources]
            record.targets = [keeper if t == merged else t for t in record.targets]

        # Update inverted index
        builder._node_to_edges[keeper].add(eid)

    # Remove merged node from inverted index
    builder._node_to_edges.pop(merged, None)

    # Remove single-member edges (self-loops after merge)
    for eid in edge_ids:
        if eid in builder._incidence and len(builder._incidence[eid]) < 2:
            builder.remove_edge(eid)
