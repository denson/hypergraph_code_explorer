"""
Node Simplification
===================
After building the graph, merge nodes with cosine similarity > threshold.
Merge lower-degree into higher-degree. Update incidence dict, edge store,
inverted index, and embeddings in one pass.

Default threshold: 0.97 (higher than MIT's 0.90; code identifiers are precise).

IMPORTANT: No transitive merge chains. Every node in a merge cluster must be
directly above threshold with the keeper node. This prevents cascading merges
where A→B→C→D collapses nodes that aren't actually similar.
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

    Uses direct-similarity-only merging: each merged node must be above
    threshold with the keeper node directly, not transitively.

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

    # Build node index for fast lookup
    node_to_idx = {n: i for i, n in enumerate(embedded_nodes)}

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

    # Build merge clusters using direct-similarity-only approach.
    # For each cluster, the keeper is the highest-degree node, and every
    # other node in the cluster must be directly above threshold with the keeper.
    # This prevents transitive chain merging.
    merge_map: dict[str, str] = {}  # merged → keeper
    merged_already: set[str] = set()

    # Sort pairs by similarity descending — process the strongest pairs first
    merge_pairs.sort(key=lambda x: -x[2])

    # Build adjacency: for each node, which other nodes are directly similar?
    similar_to: dict[str, list[tuple[str, float]]] = {}
    for a, b, sim in merge_pairs:
        similar_to.setdefault(a, []).append((b, sim))
        similar_to.setdefault(b, []).append((a, sim))

    # Process: for each unmerged node with similar neighbors, form a cluster
    # by picking the highest-degree node as keeper and only merging nodes
    # that are directly above threshold with the keeper.
    processed: set[str] = set()
    for node_a, node_b, sim in merge_pairs:
        if node_a in merged_already or node_b in merged_already:
            continue

        # Determine keeper (higher degree)
        deg_a = builder.get_node_degree(node_a)
        deg_b = builder.get_node_degree(node_b)
        if deg_a >= deg_b:
            keeper, other = node_a, node_b
        else:
            keeper, other = node_b, node_a

        if keeper in merged_already:
            continue

        # Merge 'other' into 'keeper'
        merge_map[other] = keeper
        merged_already.add(other)

        # Also check if any of keeper's other direct-similar nodes can be merged
        keeper_idx = node_to_idx[keeper]
        for candidate, cand_sim in similar_to.get(keeper, []):
            if candidate in merged_already or candidate == keeper:
                continue
            # Verify direct similarity with keeper (not transitive)
            cand_idx = node_to_idx[candidate]
            direct_sim = float(sim_matrix[keeper_idx, cand_idx])
            if direct_sim >= threshold:
                merge_map[candidate] = keeper
                merged_already.add(candidate)

    if verbose:
        print(f"  Merging {len(merge_map)} nodes (no transitive chains)")

    # Apply merges to builder
    for merged, keeper in merge_map.items():
        _merge_node(builder, merged, keeper)
        embeddings.remove(merged)

    return merge_map


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
