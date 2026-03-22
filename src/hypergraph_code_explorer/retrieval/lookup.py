"""
Tier 1 — Exact Lookup
=====================
Microsecond-speed exact name lookup via the builder's inverted index.

Process:
1. Tokenise query (split on whitespace, dots, underscores)
2. Match tokens against builder._node_to_edges keys (case-insensitive)
3. For each matched node, return all incident edges grouped by type
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..graph.builder import HypergraphBuilder, normalise_node
from ..models import HyperedgeRecord
from .plan import (
    FileSuggestion,
    GrepSuggestion,
    RetrievalPlan,
    SymbolRelation,
)


_STOPWORDS = {
    "how", "does", "do", "what", "why", "when", "where", "which", "who",
    "the", "and", "for", "that", "this", "with", "from", "into",
    "use", "uses", "used", "get", "set", "has", "have", "can",
    "will", "would", "should", "each", "some", "any", "are", "is",
    "was", "were", "been", "being", "about", "work", "works",
    "all", "its", "not", "but", "they", "them", "their", "there",
}


def _tokenise_query(query: str) -> list[str]:
    """Extract identifier-like tokens from a query.

    Splits on whitespace and punctuation, then on dots and underscores.
    Returns lowercased tokens of length >= 2, deduplicated, preserving order.
    Filters out common English stopwords so NL queries don't match noise nodes.
    """
    raw = re.split(r'[\s,;:!?(){}\[\]"\'`/\\]+', query)
    tokens: list[str] = []
    for tok in raw:
        if not tok:
            continue
        # Split on dots and underscores to handle "Session.send" or "my_func"
        parts = re.split(r'[._]', tok)
        # Also keep the full dotted form as a token
        if "." in tok:
            tokens.append(tok)
        tokens.extend(parts)

    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        low = t.lower().strip()
        if low and low not in seen and len(low) >= 2 and low not in _STOPWORDS:
            seen.add(low)
            result.append(low)
    return result


def _build_node_index(builder: HypergraphBuilder) -> dict[str, str]:
    """Build a lowercase -> original-case mapping of all node names."""
    index: dict[str, str] = {}
    for node in builder._node_to_edges:
        low = node.lower()
        # If multiple nodes map to the same lowercase, keep highest-degree
        if low not in index or len(builder._node_to_edges.get(node, set())) > len(
            builder._node_to_edges.get(index[low], set())
        ):
            index[low] = node
    return index


def _edge_type_to_relationship(edge_type: str, is_source: bool) -> str:
    """Convert an edge type + directionality to a human-readable verb."""
    rel_map = {
        "CALLS": ("calls", "called by"),
        "IMPORTS": ("imports", "imported by"),
        "DEFINES": ("defines", "defined in"),
        "INHERITS": ("inherits from", "inherited by"),
        "SIGNATURE": ("has signature", "parameter of"),
        "RAISES": ("raises", "raised by"),
        "DECORATES": ("decorates", "decorated by"),
        "TEXT": ("related to", "related to"),
        "SUMMARY": ("summarised as", "summary of"),
    }
    pair = rel_map.get(edge_type, ("related to", "related to"))
    return pair[0] if is_source else pair[1]


def _score_node_specificity(
    node: str,
    token: str,
    builder: HypergraphBuilder,
) -> float:
    """Score how specific a node match is (higher = more specific, more useful).

    Low-specificity matches are common short identifiers that connect broadly
    (like bare module names 'sql', 'os', 'sys'). High-specificity matches are
    domain-specific symbols where the token clearly refers to something
    meaningful ('QuerySet', 'ORM', 'middleware').

    All thresholds are relative to graph size so this works from small
    projects (500 nodes) to large frameworks (23k+ nodes).
    """
    degree = len(builder._node_to_edges.get(node, set()))
    total_edges = max(len(builder._incidence), 1)

    # --- Degree factor (scale-relative) ---
    # What fraction of all edges does this node touch?
    # A node touching >5% of all edges is almost certainly generic.
    # A node touching <0.5% is likely specific.
    degree_ratio = degree / total_edges
    if degree_ratio > 0.05:
        degree_factor = 0.1
    elif degree_ratio > 0.02:
        degree_factor = 0.3
    elif degree_ratio > 0.005:
        degree_factor = 0.6
    else:
        degree_factor = 1.0

    # --- Length factor (only applies when degree is also high) ---
    # Short tokens are ambiguous ONLY when they match high-degree nodes.
    # 'Q' matching Django's Q class (degree 5) is fine.
    # 'sql' matching a 300-edge module import is not.
    # So: length penalty scales with degree_ratio. If the node is specific
    # (low degree_ratio), short tokens get no penalty.
    token_len = len(token)
    if token_len <= 3 and degree_ratio > 0.005:
        # Short token AND moderately connected — penalise
        length_factor = 0.4
    elif token_len <= 3 and degree_ratio > 0.002:
        # Short token, somewhat connected — mild penalty
        length_factor = 0.7
    else:
        # Long token, or short token with low degree — no penalty
        length_factor = 1.0

    # --- Qualified name bonus ---
    # 'django.db.models.sql' is more specific than bare 'sql' regardless
    # of degree, because the qualified path provides disambiguation.
    qualified_factor = 1.0
    if "." in node:
        segments = node.split(".")
        if len(segments) >= 3:
            qualified_factor = 1.3
        elif len(segments) >= 2:
            qualified_factor = 1.1

    return degree_factor * length_factor * qualified_factor


def lookup(
    query: str,
    builder: HypergraphBuilder,
    *,
    edge_types: list[str] | None = None,
) -> RetrievalPlan:
    """Tier 1: exact name lookup against the inverted index.

    Args:
        query: The symbol name or query string to look up.
        builder: The hypergraph builder with all edges.
        edge_types: Optional filter — only return edges of these types.

    Returns:
        A RetrievalPlan populated with files, symbols, and grep suggestions.
    """
    plan = RetrievalPlan(
        query=query,
        classification=["identifier"],
        tiers_used=[1],
    )

    # Build case-insensitive node index
    node_index = _build_node_index(builder)

    matched_nodes: list[str] = []
    matched_set: set[str] = set()

    # --- Phase 0: Try the FULL query as a suffix match before tokenizing ---
    # This fixes BUG-001: "rebuild_auth" should match
    # "sessions.SessionRedirectMixin.rebuild_auth" directly, not get split
    # into ["rebuild", "auth"] where "auth" matches an unrelated symbol.
    query_lower = query.strip().lower()
    if query_lower:
        # Direct exact match
        if query_lower in node_index and node_index[query_lower] not in matched_set:
            matched_nodes.append(node_index[query_lower])
            matched_set.add(node_index[query_lower])
        # Suffix match: node ends with ".{query}" (the query is the short name)
        for node_lower, node_orig in node_index.items():
            if node_orig in matched_set:
                continue
            last_segment = node_lower.rsplit(".", 1)[-1]
            if last_segment == query_lower:
                matched_nodes.append(node_orig)
                matched_set.add(node_orig)

    # If the full-query suffix match found results, skip tokenized matching.
    # Only fall back to token-split matching if no suffix match was found.
    if not matched_nodes:
        tokens = _tokenise_query(query)
        if not tokens:
            return plan

        # Find exact matches (full dotted name first, then individual tokens)
        # Also try suffix matching: "session.send" matches "sessions.Session.send"
        for token in tokens:
            # Direct match
            if token in node_index and node_index[token] not in matched_set:
                matched_nodes.append(node_index[token])
                matched_set.add(node_index[token])
            # Segment match: token matches any segment of a node name.
            # Always run this even after a direct match, because the direct match
            # might be a bare import node while the real class/function definition
            # lives under a module-qualified name (e.g. "fastapi" vs "applications.FastAPI").
            # Limit to nodes where the token matches the LAST segment (the name itself),
            # not intermediate package segments, to avoid pulling in every sub-symbol.
            for node_lower, node_orig in node_index.items():
                if node_orig in matched_set:
                    continue
                last_segment = node_lower.rsplit(".", 1)[-1]
                if last_segment == token:
                    matched_nodes.append(node_orig)
                    matched_set.add(node_orig)

    if not matched_nodes:
        return plan

    # Collect all incident edges for matched nodes
    files_seen: dict[str, FileSuggestion] = {}
    symbols: list[SymbolRelation] = []
    grep_patterns: list[GrepSuggestion] = []

    for node in matched_nodes:
        edges = builder.get_edges_for_node(node)

        # Group edges by type
        by_type: dict[str, list[HyperedgeRecord]] = defaultdict(list)
        for e in edges:
            if edge_types and e.edge_type not in edge_types:
                continue
            by_type[e.edge_type].append(e)

        for etype, edge_list in by_type.items():
            for edge in edge_list:
                # Determine if the matched node is source or target
                is_source = node in edge.sources

                # File suggestion
                if edge.source_path and edge.source_path not in files_seen:
                    files_seen[edge.source_path] = FileSuggestion(
                        path=edge.source_path,
                        symbols=[],
                        reason=f"contains {etype} edges for {node}",
                        priority=1 if etype in ("DEFINES", "CALLS") else 2,
                    )
                if edge.source_path and node not in files_seen[edge.source_path].symbols:
                    files_seen[edge.source_path].symbols.append(node)

                # Symbol relations
                relationship = _edge_type_to_relationship(etype, is_source)
                other_nodes = edge.targets if is_source else edge.sources
                symbols.append(SymbolRelation(
                    name=node,
                    file=edge.source_path,
                    relationship=relationship,
                    edge_type=etype,
                    targets=list(other_nodes),
                ))

        # Grep suggestion: the raw node name is a good grep pattern
        last_part = node.rsplit(".", 1)[-1]
        grep_patterns.append(GrepSuggestion(
            pattern=last_part,
            scope="",
            reason=f"find usages of {node}",
        ))

    # Deduplicate and sort files
    plan.primary_files = sorted(files_seen.values(), key=lambda f: f.priority)
    plan.related_symbols = symbols
    # Deduplicate grep by pattern
    seen_patterns: set[str] = set()
    for g in grep_patterns:
        if g.pattern not in seen_patterns:
            plan.grep_suggestions.append(g)
            seen_patterns.add(g.pattern)

    # Build structural context
    if matched_nodes:
        plan.structural_context = (
            f"Found {len(matched_nodes)} node(s) matching query: "
            + ", ".join(matched_nodes)
        )

    return plan
