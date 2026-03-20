"""
Context Assembly
================
Takes a RetrievalResult and produces structured text preserving
traversal structure with evaluation signals.
"""

from __future__ import annotations

from ..models import RetrievalResult, ScoredEdge


def _confidence_tier(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    elif score >= 0.4:
        return "MED"
    return "LOW"


def assemble_context(result: RetrievalResult) -> str:
    """
    Produce structured context text from a RetrievalResult.
    Preserves traversal paths and includes evaluation signals.
    """
    lines: list[str] = []

    # Header
    lines.append("=== RETRIEVAL SUMMARY ===")
    lines.append(f"Query: \"{result.query}\"")
    lines.append(f"Coverage score: {result.coverage_score:.2f}")

    seed_count = result.retrieval_source_breakdown.get("seed", 0)
    int_count = result.retrieval_source_breakdown.get("intersection", 0)
    lines.append(f"Edge provenance: {seed_count} seed, {int_count} intersection")

    # Frontier nodes (uncovered matched nodes)
    covered_nodes: set[str] = set()
    for se in result.scored_edges:
        covered_nodes.update(se.edge.all_nodes)
    matched_set = {n for n, _ in result.matched_nodes}
    uncovered = sorted(matched_set - covered_nodes)
    if uncovered:
        lines.append(f"Uncovered frontier nodes: {uncovered}")
    lines.append("")

    # Traversal paths
    for path_idx, path in enumerate(result.traversal_paths, 1):
        lines.append(f"=== Traversal Path {path_idx} ===")
        lines.append("")

        for edge_idx, edge_id in enumerate(path.edges, 1):
            se = _find_scored_edge(result.scored_edges, edge_id)
            if se is None:
                continue

            tier = _confidence_tier(se.score)
            lines.append(
                f"[Edge {edge_idx}] {se.edge.edge_type}: {se.edge.relation}  "
                f"[{se.retrieval_source} | score: {se.score:.2f} | {tier}]"
            )
            lines.append(f"  Source: {se.edge.source_path}")

            if se.edge.chunk_text:
                text_preview = se.edge.chunk_text[:500]
                if len(se.edge.chunk_text) > 500:
                    text_preview += "..."
                lines.append("  ---")
                for line in text_preview.split("\n"):
                    lines.append(f"  {line}")
                lines.append("  ---")

            # Connection to next edge
            if edge_idx <= len(path.hops):
                hop = path.hops[edge_idx - 1]
                lines.append(
                    f"  ↓ connected via: {{{', '.join(hop.intersection_nodes)}}}"
                )
            lines.append("")

    # Additional edges not in traversal paths
    path_edge_ids = set()
    for p in result.traversal_paths:
        path_edge_ids.update(p.edges)

    remaining = [se for se in result.scored_edges if se.edge.edge_id not in path_edge_ids]
    if remaining:
        lines.append("=== Additional Relevant Edges ===")
        lines.append("")
        for se in remaining[:10]:
            tier = _confidence_tier(se.score)
            lines.append(
                f"[{se.edge.edge_type}] {se.edge.relation}  "
                f"[{se.retrieval_source} | score: {se.score:.2f} | {tier}]"
            )
            lines.append(f"  Source: {se.edge.source_path}")
            lines.append("")

    return "\n".join(lines)


def _find_scored_edge(
    scored_edges: list[ScoredEdge], edge_id: str
) -> ScoredEdge | None:
    for se in scored_edges:
        if se.edge.edge_id == edge_id:
            return se
    return None
