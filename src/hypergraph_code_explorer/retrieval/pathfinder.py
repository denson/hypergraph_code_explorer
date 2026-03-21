"""
# DEPRECATED — replaced by retrieval/traverse.py in v3.
# Kept for backward compatibility with legacy pipeline.find_path().

Edge-BFS Path Finder
====================
Finds shortest paths between two entities through hyperedge space.
Two edges are adjacent if they share ≥ s nodes.
Adapted from MIT's find_shortest_path_hypersubgraph_between_nodes_local.
"""

from __future__ import annotations

from collections import deque

from ..graph.builder import HypergraphBuilder
from ..models import DEFAULT_INTERSECTION_THRESHOLDS, PathReport, TraversalHop


def find_paths(
    source: str,
    target: str,
    builder: HypergraphBuilder,
    k_paths: int = 3,
    max_depth: int = 10,
    intersection_thresholds: dict[str, int] | None = None,
) -> list[PathReport]:
    """
    Edge-BFS between two entities through hyperedge space.

    Args:
        source: Source entity name
        target: Target entity name
        builder: The hypergraph builder
        k_paths: Maximum number of paths to return
        max_depth: Maximum BFS depth
        intersection_thresholds: Per-edge-type minimum intersection size

    Returns:
        List of PathReport objects with intersection nodes at each hop
    """
    thresholds = intersection_thresholds or DEFAULT_INTERSECTION_THRESHOLDS

    # Find all edges containing source and target
    source_edges = builder.get_edge_ids_for_node(source)
    target_edges = builder.get_edge_ids_for_node(target)

    if not source_edges or not target_edges:
        return []

    # Check if source and target share any edge (direct connection)
    direct = source_edges & target_edges
    if direct:
        paths = []
        for eid in sorted(direct)[:k_paths]:
            nodes = builder._incidence.get(eid, set())
            paths.append(PathReport(
                edges=[eid],
                hops=[],
                start_comembers=sorted(nodes),
                end_comembers=sorted(nodes),
            ))
        return paths

    # BFS through edge space
    # State: current edge_id
    # Two edges are adjacent if they share ≥ s nodes
    paths: list[PathReport] = []

    # BFS from source edges to target edges
    queue: deque[list[str]] = deque()
    for s_eid in source_edges:
        queue.append([s_eid])

    visited: set[str] = set(source_edges)
    found_depth = float("inf")

    while queue and len(paths) < k_paths:
        path = queue.popleft()

        if len(path) > max_depth:
            break
        if len(path) > found_depth + 1:
            break

        current_eid = path[-1]

        # Check if current edge contains target
        if current_eid in target_edges:
            # Build PathReport
            report = _build_path_report(path, builder)
            paths.append(report)
            found_depth = min(found_depth, len(path))
            continue

        # Expand: find adjacent edges
        current_record = builder.get_edge(current_eid)
        if current_record is None:
            continue

        threshold = thresholds.get(current_record.edge_type, 1)
        adjacent = builder.get_adjacent_edges(current_eid, s=threshold)

        for adj_eid, _ in adjacent:
            if adj_eid not in visited:
                visited.add(adj_eid)
                queue.append(path + [adj_eid])

    return paths


def _build_path_report(edge_path: list[str], builder: HypergraphBuilder) -> PathReport:
    """Convert a list of edge IDs into a PathReport with hop details."""
    hops: list[TraversalHop] = []

    for i in range(len(edge_path) - 1):
        from_eid = edge_path[i]
        to_eid = edge_path[i + 1]
        intersection = builder.get_intersection(from_eid, to_eid)
        from_nodes = builder._incidence.get(from_eid, set())
        to_nodes = builder._incidence.get(to_eid, set())

        hops.append(TraversalHop(
            from_edge=from_eid,
            to_edge=to_eid,
            intersection_nodes=sorted(intersection),
            from_members=sorted(from_nodes),
            to_members=sorted(to_nodes),
        ))

    start_nodes = builder._incidence.get(edge_path[0], set())
    end_nodes = builder._incidence.get(edge_path[-1], set())

    return PathReport(
        edges=edge_path,
        hops=hops,
        start_comembers=sorted(start_nodes),
        end_comembers=sorted(end_nodes),
    )
