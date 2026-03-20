"""
Coverage Evaluation
===================
The hypergraph_coverage tool: purely local, no LLM calls.
Evaluates how well a set of retrieved edges covers the seed nodes
and identifies frontier nodes for follow-up queries.
"""

from __future__ import annotations

from collections import defaultdict

from ..graph.builder import HypergraphBuilder
from ..models import CoverageResult


def evaluate_coverage(
    retrieved_edge_ids: list[str],
    seed_node_ids: list[str],
    builder: HypergraphBuilder,
    depth: int = 1,
) -> CoverageResult:
    """
    Evaluate coverage of retrieved edges over seed nodes.

    Args:
        retrieved_edge_ids: Edge IDs from a previous retrieve call
        seed_node_ids: Matched nodes from retrieve
        builder: The hypergraph builder
        depth: How far to look for frontier nodes (default: 1 hop)

    Returns:
        CoverageResult with covered/uncovered/frontier nodes and scores
    """
    seed_set = set(seed_node_ids)

    # Collect all nodes in retrieved edges
    covered_nodes: set[str] = set()
    retrieval_source_count: dict[str, int] = defaultdict(int)
    for eid in retrieved_edge_ids:
        record = builder.get_edge(eid)
        if record is None:
            continue
        covered_nodes.update(record.all_nodes)
        # Count retrieval sources from metadata if available
        source = record.metadata.get("retrieval_source", "seed")
        retrieval_source_count[source] += 1

    # Determine covered and uncovered seed nodes
    covered_seed = seed_set & covered_nodes
    uncovered_seed = seed_set - covered_nodes

    # Find frontier nodes: nodes 1 hop outside the retrieved subgraph
    frontier_nodes: list[dict] = []
    if depth >= 1:
        # Get all nodes adjacent to retrieved edges but not in retrieved set
        retrieved_edge_set = set(retrieved_edge_ids)
        frontier_edge_ids: set[str] = set()

        for eid in retrieved_edge_ids:
            adjacent = builder.get_adjacent_edges(eid, s=1)
            for adj_eid, _ in adjacent:
                if adj_eid not in retrieved_edge_set:
                    frontier_edge_ids.add(adj_eid)

        # Collect frontier nodes from those edges
        frontier_node_set: set[str] = set()
        for eid in frontier_edge_ids:
            record = builder.get_edge(eid)
            if record:
                frontier_node_set.update(record.all_nodes)

        # Remove nodes already covered
        frontier_node_set -= covered_nodes

        # Build frontier node details
        for node in sorted(frontier_node_set):
            degree = builder.get_node_degree(node)
            frontier_nodes.append({
                "node": node,
                "incident_edge_count": degree,
                "suggested_query": f"{node} implementation",
            })

        # Sort by incident edge count descending (most connected first)
        frontier_nodes.sort(key=lambda x: x["incident_edge_count"], reverse=True)

    # Coverage score
    total = len(covered_seed) + len(uncovered_seed)
    coverage_score = len(covered_seed) / total if total > 0 else 0.0

    # Intersection density
    intersection_density = _compute_intersection_density(retrieved_edge_ids, builder)

    return CoverageResult(
        covered_nodes=sorted(covered_seed),
        uncovered_nodes=sorted(uncovered_seed),
        frontier_nodes=frontier_nodes[:20],
        coverage_score=coverage_score,
        intersection_density=intersection_density,
        retrieval_source_breakdown=dict(retrieval_source_count),
    )


def _compute_intersection_density(
    edge_ids: list[str],
    builder: HypergraphBuilder,
) -> float:
    """Fraction of edge pairs that share at least 1 node."""
    if len(edge_ids) < 2:
        return 0.0

    total_pairs = 0
    intersecting = 0
    for i in range(len(edge_ids)):
        for j in range(i + 1, len(edge_ids)):
            total_pairs += 1
            if builder.get_intersection(edge_ids[i], edge_ids[j]):
                intersecting += 1

    return intersecting / total_pairs if total_pairs > 0 else 0.0
