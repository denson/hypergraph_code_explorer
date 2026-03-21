"""
Tier 2 — Structural Traversal
==============================
Relationship-typed BFS/DFS through the hypergraph. Starts from seed nodes
(typically from Tier 1) and follows edges of specific types to a configurable
depth.

Relationship type is inferred from query verbs:
  "calls/invokes"   -> CALLS
  "inherits/extends" -> INHERITS
  "imports/depends"  -> IMPORTS
  "raises/throws"    -> RAISES
  no verb            -> all types
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from ..graph.builder import HypergraphBuilder
from ..models import HyperedgeRecord
from .plan import (
    FileSuggestion,
    GrepSuggestion,
    RetrievalPlan,
    SymbolRelation,
)


# ---------------------------------------------------------------------------
# Verb -> edge type mapping
# ---------------------------------------------------------------------------

VERB_TO_EDGE_TYPES: dict[str, list[str]] = {
    "call": ["CALLS"],
    "calls": ["CALLS"],
    "invoke": ["CALLS"],
    "invokes": ["CALLS"],
    "delegate": ["CALLS"],
    "delegates": ["CALLS"],
    "inherit": ["INHERITS"],
    "inherits": ["INHERITS"],
    "extend": ["INHERITS"],
    "extends": ["INHERITS"],
    "subclass": ["INHERITS"],
    "subclasses": ["INHERITS"],
    "import": ["IMPORTS"],
    "imports": ["IMPORTS"],
    "depend": ["IMPORTS"],
    "depends": ["IMPORTS"],
    "require": ["IMPORTS"],
    "requires": ["IMPORTS"],
    "raise": ["RAISES"],
    "raises": ["RAISES"],
    "throw": ["RAISES"],
    "throws": ["RAISES"],
    "decorate": ["DECORATES"],
    "decorates": ["DECORATES"],
    "define": ["DEFINES"],
    "defines": ["DEFINES"],
}


def infer_edge_types(query: str) -> list[str] | None:
    """Infer which edge types to follow from verbs in the query.

    Returns a list of edge type strings, or None if no verb is detected
    (meaning: follow all types).
    """
    words = query.lower().split()
    matched_types: list[str] = []
    for word in words:
        # Strip common suffixes for fuzzy matching
        clean = word.rstrip(".,;:!?")
        if clean in VERB_TO_EDGE_TYPES:
            for et in VERB_TO_EDGE_TYPES[clean]:
                if et not in matched_types:
                    matched_types.append(et)
    return matched_types if matched_types else None


def _relationship_label(edge_type: str, forward: bool) -> str:
    """Human label for a traversal direction."""
    labels = {
        "CALLS": ("calls", "called by"),
        "IMPORTS": ("imports", "imported by"),
        "DEFINES": ("defines", "defined in"),
        "INHERITS": ("inherits from", "inherited by"),
        "SIGNATURE": ("has signature", "parameter of"),
        "RAISES": ("raises", "raised by"),
        "DECORATES": ("decorates", "decorated by"),
    }
    pair = labels.get(edge_type, ("related to", "related to"))
    return pair[0] if forward else pair[1]


# ---------------------------------------------------------------------------
# Core traversal
# ---------------------------------------------------------------------------

def traverse(
    seed_nodes: list[str],
    builder: HypergraphBuilder,
    *,
    edge_types: list[str] | None = None,
    depth: int = 2,
    direction: str = "forward",  # "forward", "backward", "both"
    hub_nodes: set[str] | None = None,
) -> RetrievalPlan:
    """Tier 2: BFS traversal from seed nodes through typed edges.

    Args:
        seed_nodes: Starting node names (from Tier 1 or user input).
        builder: The hypergraph builder.
        edge_types: Only follow these edge types (None = all).
        depth: Maximum BFS depth.
        direction: "forward" follows source->target, "backward" target->source,
                   "both" follows in both directions.
        hub_nodes: Nodes to skip during traversal (high-degree hubs).

    Returns:
        A RetrievalPlan with traversal results.
    """
    plan = RetrievalPlan(
        query=", ".join(seed_nodes),
        classification=["structural"],
        tiers_used=[2],
    )

    if not seed_nodes:
        return plan

    if hub_nodes is None:
        # Only compute hub nodes for graphs large enough that hubs are meaningful
        if len(builder._incidence) >= 50:
            hub_nodes = builder.get_hub_nodes()
        else:
            hub_nodes = set()

    # BFS state
    visited_nodes: set[str] = set()
    visited_edges: set[str] = set()
    queue: deque[tuple[str, int]] = deque()  # (node, current_depth)

    # Resolve seed nodes (case-insensitive lookup)
    all_nodes_lower = {n.lower(): n for n in builder._node_to_edges}
    resolved_seeds: list[str] = []
    for seed in seed_nodes:
        resolved = all_nodes_lower.get(seed.lower())
        if resolved:
            resolved_seeds.append(resolved)
            queue.append((resolved, 0))
            visited_nodes.add(resolved)

    if not resolved_seeds:
        return plan

    files_seen: dict[str, FileSuggestion] = {}
    symbols: list[SymbolRelation] = []
    context_lines: list[str] = []
    grep_patterns: set[str] = set()

    while queue:
        current_node, current_depth = queue.popleft()

        if current_depth >= depth:
            continue

        # Get all edges for this node
        edges = builder.get_edges_for_node(current_node)

        for edge in edges:
            if edge.edge_id in visited_edges:
                continue
            if edge_types and edge.edge_type not in edge_types:
                continue

            visited_edges.add(edge.edge_id)

            # Determine direction
            is_source = current_node in edge.sources
            is_target = current_node in edge.targets

            if direction == "forward" and not is_source:
                continue
            if direction == "backward" and not is_target:
                continue

            # Determine next nodes to visit
            if is_source:
                next_nodes = [t for t in edge.targets
                              if t not in visited_nodes and t not in hub_nodes]
                rel_label = _relationship_label(edge.edge_type, forward=True)
                other_nodes = edge.targets
            else:
                next_nodes = [s for s in edge.sources
                              if s not in visited_nodes and s not in hub_nodes]
                rel_label = _relationship_label(edge.edge_type, forward=False)
                other_nodes = edge.sources

            # Record the relationship
            symbols.append(SymbolRelation(
                name=current_node,
                file=edge.source_path,
                relationship=rel_label,
                edge_type=edge.edge_type,
                targets=list(other_nodes),
            ))

            # File suggestion
            if edge.source_path and edge.source_path not in files_seen:
                files_seen[edge.source_path] = FileSuggestion(
                    path=edge.source_path,
                    symbols=[],
                    reason=f"{edge.edge_type} edge at depth {current_depth + 1}",
                    priority=current_depth + 1,
                )
            if edge.source_path:
                for n in [current_node] + list(other_nodes):
                    if n not in files_seen[edge.source_path].symbols:
                        files_seen[edge.source_path].symbols.append(n)

            # Grep pattern for important targets
            for n in other_nodes:
                last_part = n.rsplit(".", 1)[-1]
                if len(last_part) >= 3 and last_part not in hub_nodes:
                    grep_patterns.add(last_part)

            # Context line
            indent = "  " * current_depth
            targets_str = ", ".join(other_nodes[:5])
            if len(other_nodes) > 5:
                targets_str += f", ... (+{len(other_nodes) - 5} more)"
            context_lines.append(
                f"{indent}{current_node} {rel_label} {targets_str}"
            )

            # Enqueue next nodes
            for n in next_nodes:
                visited_nodes.add(n)
                queue.append((n, current_depth + 1))

    plan.primary_files = sorted(files_seen.values(), key=lambda f: f.priority)
    plan.related_symbols = symbols
    # Cap grep suggestions to keep output actionable for agents
    MAX_GREP_SUGGESTIONS = 15
    sorted_patterns = sorted(grep_patterns)[:MAX_GREP_SUGGESTIONS]
    plan.grep_suggestions = [
        GrepSuggestion(pattern=p, reason="traversal target")
        for p in sorted_patterns
    ]
    if context_lines:
        plan.structural_context = "\n".join(context_lines)

    return plan
