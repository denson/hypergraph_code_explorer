"""
Edge-Intersection Retrieval
============================
THE CORE MODULE. Implements the full retrieval algorithm:
  Phase 1 — Seed edge selection via embedding similarity
  Phase 2 — Intersection expansion via shared nodes
  Phase 3 — Traversal path construction following intersection chains

Scoring: score = (α × weighted_precision + (1−α) × coverage) × type_weight
"""

from __future__ import annotations

from collections import defaultdict

from ..graph.builder import HypergraphBuilder
from ..graph.embeddings import EmbeddingManager
from ..models import (
    DEFAULT_INTERSECTION_THRESHOLDS,
    DEFAULT_TYPE_WEIGHTS,
    HyperedgeRecord,
    PathReport,
    RetrievalResult,
    ScoredEdge,
    TraversalHop,
)


def retrieve(
    query: str,
    builder: HypergraphBuilder,
    embeddings: EmbeddingManager,
    top_k: int = 20,
    alpha: float = 0.6,
    max_expansion: int = 50,
    max_hops: int = 5,
    type_weights: dict[str, float] | None = None,
    intersection_thresholds: dict[str, int] | None = None,
) -> RetrievalResult:
    """
    Full retrieval pipeline: seed selection → intersection expansion → path construction.

    Args:
        query: Natural language query
        builder: The hypergraph builder with all edges
        embeddings: The embedding manager with all node embeddings
        top_k: Number of top similar nodes to use as seeds
        alpha: Balance between weighted precision and coverage (0-1)
        max_expansion: Maximum number of expansion edges to consider
        max_hops: Maximum traversal path length
        type_weights: Per-edge-type scoring weights
        intersection_thresholds: Per-edge-type minimum intersection size
    """
    tw = type_weights or DEFAULT_TYPE_WEIGHTS
    it = intersection_thresholds or DEFAULT_INTERSECTION_THRESHOLDS

    # Phase 1 — Seed edge selection
    matched_nodes = embeddings.top_k_similar(query, k=top_k)
    if not matched_nodes:
        return RetrievalResult(
            query=query, matched_nodes=[], scored_edges=[],
            coverage_score=0.0, intersection_density=0.0,
            retrieval_source_breakdown={"seed": 0, "intersection": 0},
        )

    node_scores = {node: score for node, score in matched_nodes}
    k = len(matched_nodes)

    # Collect all edges incident on matched nodes
    seed_edge_ids: set[str] = set()
    for node, _ in matched_nodes:
        seed_edge_ids.update(builder.get_edge_ids_for_node(node))

    # Score seed edges
    seed_scored: dict[str, ScoredEdge] = {}
    for eid in seed_edge_ids:
        record = builder.get_edge(eid)
        if record is None:
            continue

        matched_in_edge = [n for n in record.all_nodes if n in node_scores]
        if not matched_in_edge:
            continue

        # Weighted precision: sum of similarities of matched nodes / edge size
        wp = sum(node_scores[n] for n in matched_in_edge) / len(record.all_nodes)
        # Coverage: fraction of query's matched nodes present in this edge
        cov = len(matched_in_edge) / k
        # Type weight
        t_weight = tw.get(record.edge_type, 1.0)
        score = (alpha * wp + (1 - alpha) * cov) * t_weight

        seed_scored[eid] = ScoredEdge(
            edge=record,
            weighted_precision=wp,
            coverage=cov,
            score=score,
            retrieval_source="seed",
            matched_nodes=matched_in_edge,
        )

    # Phase 2 — Intersection expansion
    expansion_scored: dict[str, ScoredEdge] = {}
    for eid, se in sorted(seed_scored.items(), key=lambda x: x[1].score, reverse=True):
        record = se.edge
        threshold = it.get(record.edge_type, 1)
        adjacent = builder.get_adjacent_edges(eid, s=threshold)

        for adj_eid, intersection_nodes in adjacent:
            if adj_eid in seed_scored or adj_eid in expansion_scored:
                continue

            adj_record = builder.get_edge(adj_eid)
            if adj_record is None:
                continue

            # Intersection score: |intersection| × avg similarity of intersection nodes
            int_sims = [node_scores.get(n, 0.0) for n in intersection_nodes]
            avg_sim = sum(int_sims) / len(int_sims) if int_sims else 0.0
            intersection_score = len(intersection_nodes) * avg_sim

            # Combined: blend seed-edge score with intersection quality
            final_score = alpha * se.score + (1 - alpha) * intersection_score

            # Get matched nodes in the expansion edge
            matched_in_adj = [n for n in adj_record.all_nodes if n in node_scores]

            expansion_scored[adj_eid] = ScoredEdge(
                edge=adj_record,
                weighted_precision=intersection_score,
                coverage=len(matched_in_adj) / k if k > 0 else 0.0,
                score=final_score,
                retrieval_source="intersection",
                matched_nodes=list(intersection_nodes),
            )

            if len(expansion_scored) >= max_expansion:
                break
        if len(expansion_scored) >= max_expansion:
            break

    # Combine and sort all scored edges
    all_scored = {**seed_scored, **expansion_scored}
    sorted_edges = sorted(all_scored.values(), key=lambda x: x.score, reverse=True)

    # Phase 3 — Traversal path construction
    traversal_paths = _build_traversal_paths(
        sorted_edges, builder, it, max_hops=max_hops,
    )

    # Compute coverage score
    all_retrieved_nodes: set[str] = set()
    for se in sorted_edges:
        all_retrieved_nodes.update(se.edge.all_nodes)
    seed_node_set = set(node_scores.keys())
    covered = seed_node_set & all_retrieved_nodes
    coverage_score = len(covered) / len(seed_node_set) if seed_node_set else 0.0

    # Compute intersection density
    edge_ids = [se.edge.edge_id for se in sorted_edges]
    intersection_density = _compute_intersection_density(edge_ids, builder)

    seed_count = sum(1 for se in sorted_edges if se.retrieval_source == "seed")
    int_count = sum(1 for se in sorted_edges if se.retrieval_source == "intersection")

    return RetrievalResult(
        query=query,
        matched_nodes=matched_nodes,
        scored_edges=sorted_edges,
        traversal_paths=traversal_paths,
        coverage_score=coverage_score,
        intersection_density=intersection_density,
        retrieval_source_breakdown={"seed": seed_count, "intersection": int_count},
    )


def _build_traversal_paths(
    scored_edges: list[ScoredEdge],
    builder: HypergraphBuilder,
    thresholds: dict[str, int],
    max_hops: int = 5,
) -> list[PathReport]:
    """Build traversal paths following highest-scoring intersections."""
    if not scored_edges:
        return []

    paths: list[PathReport] = []
    used_edges: set[str] = set()

    # Start from the highest-scoring seed edge
    for start_se in scored_edges:
        if start_se.edge.edge_id in used_edges:
            continue
        if start_se.retrieval_source != "seed":
            continue

        path_edges = [start_se.edge.edge_id]
        hops: list[TraversalHop] = []
        current_eid = start_se.edge.edge_id
        used_edges.add(current_eid)

        for _ in range(max_hops):
            current_record = builder.get_edge(current_eid)
            if current_record is None:
                break

            threshold = thresholds.get(current_record.edge_type, 1)
            adjacent = builder.get_adjacent_edges(current_eid, s=threshold)

            # Pick the highest-scoring adjacent edge from our scored set
            best_next = None
            best_score = -1.0
            best_intersection: set[str] = set()
            for adj_eid, intersection_nodes in adjacent:
                if adj_eid in used_edges:
                    continue
                # Look up in scored edges
                for se in scored_edges:
                    if se.edge.edge_id == adj_eid and se.score > best_score:
                        best_next = se
                        best_score = se.score
                        best_intersection = intersection_nodes
                        break

            if best_next is None:
                break

            current_nodes = builder._incidence.get(current_eid, set())
            next_nodes = builder._incidence.get(best_next.edge.edge_id, set())

            hops.append(TraversalHop(
                from_edge=current_eid,
                to_edge=best_next.edge.edge_id,
                intersection_nodes=sorted(best_intersection),
                from_members=sorted(current_nodes),
                to_members=sorted(next_nodes),
            ))

            path_edges.append(best_next.edge.edge_id)
            used_edges.add(best_next.edge.edge_id)
            current_eid = best_next.edge.edge_id

        if len(path_edges) > 1:
            # Comembers: other nodes in the start/end edges
            start_nodes = builder._incidence.get(path_edges[0], set())
            end_nodes = builder._incidence.get(path_edges[-1], set())
            paths.append(PathReport(
                edges=path_edges,
                hops=hops,
                start_comembers=sorted(start_nodes),
                end_comembers=sorted(end_nodes),
            ))

        if len(paths) >= 3:
            break

    return paths


def _compute_intersection_density(
    edge_ids: list[str],
    builder: HypergraphBuilder,
) -> float:
    """Fraction of edge pairs that share at least 1 node."""
    if len(edge_ids) < 2:
        return 0.0

    total_pairs = 0
    intersecting_pairs = 0
    for i in range(len(edge_ids)):
        for j in range(i + 1, len(edge_ids)):
            total_pairs += 1
            intersection = builder.get_intersection(edge_ids[i], edge_ids[j])
            if intersection:
                intersecting_pairs += 1

    return intersecting_pairs / total_pairs if total_pairs > 0 else 0.0
