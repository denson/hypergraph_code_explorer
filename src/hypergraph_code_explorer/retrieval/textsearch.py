"""
Tier 3 — Text Search
====================
Substring/regex search over all node names, file paths, relation strings,
and chunk text. Ranked by match quality:

  exact stem > prefix > substring > chunk_text

Top matches are returned as a RetrievalPlan with file suggestions and
grep patterns. Tier 3 results can feed into Tier 1/2 for structural expansion.
"""

from __future__ import annotations

import re
from collections import defaultdict
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
# Match quality tiers
# ---------------------------------------------------------------------------

QUALITY_EXACT_STEM = 1    # node's last segment == term exactly
QUALITY_PREFIX = 2        # node starts with term
QUALITY_SUBSTRING = 3     # term appears as substring in node
QUALITY_RELATION = 4      # term appears in edge relation string
QUALITY_CHUNK = 5         # term appears in chunk text
QUALITY_PATH = 6          # term appears in file path


def _extract_search_terms(query: str) -> list[str]:
    """Extract meaningful search terms from a query.

    Filters out common English words and keeps terms of length >= 3.
    """
    STOPWORDS = {
        "how", "does", "what", "why", "when", "where", "which",
        "the", "and", "for", "that", "this", "with", "from", "into",
        "use", "uses", "used", "get", "set", "has", "have", "can",
        "will", "would", "should", "each", "some", "any", "are",
        "was", "were", "been", "being", "about", "work", "works",
        "all", "its", "not", "but", "they", "them", "their", "there",
    }
    raw = re.split(r'[\s,;:!?(){}\[\]"\'`/\\]+', query)
    terms: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        # Split on dots and underscores
        for part in re.split(r'[._]', tok):
            low = part.lower().strip()
            if low and low not in seen and low not in STOPWORDS and len(low) >= 3:
                seen.add(low)
                terms.append(low)
        # Also keep the full dotted form
        low_full = tok.lower().strip()
        if "." in tok and low_full not in seen and len(low_full) >= 3:
            seen.add(low_full)
            terms.append(low_full)
    return terms


def text_search(
    query: str,
    builder: HypergraphBuilder,
    *,
    max_results: int = 20,
    search_chunks: bool = False,
) -> RetrievalPlan:
    """Tier 3: substring search across all graph entities.

    Args:
        query: Search term(s).
        builder: The hypergraph builder.
        max_results: Maximum number of matched nodes to return.
        search_chunks: Also search chunk_text (slower but more thorough).

    Returns:
        A RetrievalPlan with matched nodes, files, and grep suggestions.
    """
    plan = RetrievalPlan(
        query=query,
        classification=["text_search"],
        tiers_used=[3],
    )

    terms = _extract_search_terms(query)
    if not terms:
        return plan

    # Collect matches: (node_or_path, quality, term)
    matches: list[tuple[str, int, str]] = []

    # Search node names
    all_nodes = list(builder._node_to_edges.keys())
    for node in all_nodes:
        node_lower = node.lower()
        # Last segment for stem matching
        stem = node.rsplit(".", 1)[-1].lower()

        for term in terms:
            if stem == term:
                matches.append((node, QUALITY_EXACT_STEM, term))
            elif node_lower.startswith(term):
                matches.append((node, QUALITY_PREFIX, term))
            elif term in node_lower:
                matches.append((node, QUALITY_SUBSTRING, term))

    # Search file paths
    paths_seen: set[str] = set()
    for rec in builder._edge_store.values():
        if rec.source_path and rec.source_path not in paths_seen:
            paths_seen.add(rec.source_path)
            path_lower = rec.source_path.lower()
            path_stem = Path(rec.source_path).stem.lower()
            for term in terms:
                if path_stem == term:
                    matches.append((rec.source_path, QUALITY_EXACT_STEM, term))
                elif term in path_lower:
                    matches.append((rec.source_path, QUALITY_PATH, term))

    # Search relation strings — only add nodes whose names contain the term,
    # not every node that happens to share an edge with a matching relation.
    for rec in builder._edge_store.values():
        rel_lower = rec.relation.lower()
        for term in terms:
            if term in rel_lower:
                for node in rec.all_nodes:
                    if term in node.lower():
                        matches.append((node, QUALITY_RELATION, term))

    # Optionally search chunk text
    if search_chunks:
        for rec in builder._edge_store.values():
            if rec.chunk_text:
                chunk_lower = rec.chunk_text.lower()
                for term in terms:
                    if term in chunk_lower:
                        for node in rec.all_nodes:
                            matches.append((node, QUALITY_CHUNK, term))

    if not matches:
        return plan

    # Rank: sort by quality (best first), then deduplicate
    matches.sort(key=lambda x: x[1])

    # Collect unique matched nodes, ordered by best quality
    matched_nodes: list[str] = []
    matched_set: set[str] = set()
    matched_paths: list[str] = []
    matched_paths_set: set[str] = set()

    for entity, quality, term in matches:
        if len(matched_nodes) + len(matched_paths) >= max_results:
            break
        # Distinguish file paths from node names
        if quality == QUALITY_PATH or quality == QUALITY_EXACT_STEM and "/" in entity or "\\" in entity:
            if entity not in matched_paths_set:
                if "/" in entity or "\\" in entity:
                    matched_paths.append(entity)
                    matched_paths_set.add(entity)
                else:
                    # It's a node name with exact stem match
                    if entity not in matched_set:
                        matched_nodes.append(entity)
                        matched_set.add(entity)
        else:
            if entity not in matched_set:
                matched_nodes.append(entity)
                matched_set.add(entity)

    # Build plan from matched nodes — only include the matched node in the
    # file suggestion symbols, not every node sharing an edge with it.
    matched_node_set = set(matched_nodes)
    files_seen: dict[str, FileSuggestion] = {}
    symbols: list[SymbolRelation] = []

    for node in matched_nodes:
        edges = builder.get_edges_for_node(node)
        # Find the best file for this node (prefer DEFINES edges)
        node_files: set[str] = set()
        for edge in edges:
            if edge.source_path:
                node_files.add(edge.source_path)
        for fpath in node_files:
            if fpath not in files_seen:
                files_seen[fpath] = FileSuggestion(
                    path=fpath,
                    symbols=[],
                    reason=f"contains '{node}' (text match)",
                    priority=2,
                )
            if node not in files_seen[fpath].symbols:
                files_seen[fpath].symbols.append(node)

        # Add as related symbol
        if edges:
            edge_types = list({e.edge_type for e in edges})
            symbols.append(SymbolRelation(
                name=node,
                file=edges[0].source_path if edges else "",
                relationship="text match",
                edge_type=edge_types[0] if edge_types else "",
            ))

    # Add file-path matches as file suggestions
    for path in matched_paths:
        if path not in files_seen:
            files_seen[path] = FileSuggestion(
                path=path,
                symbols=[],
                reason="file path matches search term",
                priority=1,
            )

    # Group files by directory — if 3+ files from same dir, collapse into one entry
    DIR_COLLAPSE_THRESHOLD = 3
    dir_counts: dict[str, list[str]] = defaultdict(list)
    for path in files_seen:
        dir_path = str(Path(path).parent)
        dir_counts[dir_path].append(path)

    collapsed_files: list[FileSuggestion] = []
    collapsed_dirs: set[str] = set()
    for dir_path, paths in dir_counts.items():
        if len(paths) >= DIR_COLLAPSE_THRESHOLD:
            # Collect all symbols and compute a quality score
            all_symbols: list[str] = []
            total_symbol_count = 0
            for p in paths:
                all_symbols.extend(files_seen[p].symbols)
                total_symbol_count += len(files_seen[p].symbols)
            unique_symbols = list(dict.fromkeys(all_symbols))[:10]

            # Quality score for ranking:
            #   - More matching files = more relevant directory
            #   - Directories whose name contains a search term get a bonus
            dir_name = Path(dir_path).name.lower()
            name_bonus = any(term in dir_name for term in terms)

            # Priority: 1 = best.
            # Directories with name match AND high file count → priority 1
            # Directories with name match OR high file count → priority 2
            # Everything else → priority 3
            file_count = len(paths)
            if name_bonus and file_count >= 5:
                priority = 1
            elif name_bonus or file_count >= 7:
                priority = 2
            else:
                priority = 3

            collapsed_files.append(FileSuggestion(
                path=dir_path + "/",
                symbols=unique_symbols,
                reason=f"{file_count} files match (text search)",
                priority=priority,
            ))
            collapsed_dirs.add(dir_path)

    # Add non-collapsed files
    for path, suggestion in files_seen.items():
        dir_path = str(Path(path).parent)
        if dir_path not in collapsed_dirs:
            collapsed_files.append(suggestion)

    plan.primary_files = sorted(collapsed_files, key=lambda f: f.priority)
    plan.related_symbols = symbols

    # Grep suggestions from search terms
    for term in terms:
        plan.grep_suggestions.append(GrepSuggestion(
            pattern=term,
            scope="",
            reason="search term",
        ))

    # Context
    if matched_nodes:
        plan.structural_context = (
            f"Text search matched {len(matched_nodes)} symbol(s): "
            + ", ".join(matched_nodes[:10])
        )
        if len(matched_nodes) > 10:
            plan.structural_context += f" ... (+{len(matched_nodes) - 10} more)"

    return plan


def get_matched_nodes(
    query: str,
    builder: HypergraphBuilder,
    *,
    max_results: int = 20,
) -> list[str]:
    """Convenience: return just the matched node names (for feeding into Tier 1/2)."""
    terms = _extract_search_terms(query)
    if not terms:
        return []

    matches: list[tuple[str, int]] = []
    all_nodes = list(builder._node_to_edges.keys())

    for node in all_nodes:
        node_lower = node.lower()
        stem = node.rsplit(".", 1)[-1].lower()
        best_quality = 999

        for term in terms:
            if stem == term:
                best_quality = min(best_quality, QUALITY_EXACT_STEM)
            elif node_lower.startswith(term):
                best_quality = min(best_quality, QUALITY_PREFIX)
            elif term in node_lower:
                best_quality = min(best_quality, QUALITY_SUBSTRING)

        if best_quality < 999:
            matches.append((node, best_quality))

    matches.sort(key=lambda x: x[1])
    return [m[0] for m in matches[:max_results]]
