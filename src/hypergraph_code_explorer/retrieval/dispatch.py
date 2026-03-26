"""
Query Dispatcher
================
Classifies a query and routes it through the appropriate retrieval tiers.

Tier cascade:
  1. Exact Lookup — if query tokens match node names exactly
  2. Structural Traversal — if relationship verbs detected + seed nodes found
  3. Text Search — substring matching across all entities
  4. Embedding Fallback — (Phase 3, not implemented here)

Each tier enriches a single RetrievalPlan via merge(). The cascade stops
early if sufficient results are found, but always runs Tier 2 when Tier 1
produces seed nodes and the query contains structural verbs.
"""

from __future__ import annotations

import re

from ..graph.builder import HypergraphBuilder
from .lookup import lookup, _tokenise_query, _build_node_index, _score_node_specificity
from .plan import FileSuggestion, RetrievalPlan
from .textsearch import text_search, get_matched_nodes
from .traverse import traverse, infer_edge_types


# ---------------------------------------------------------------------------
# Query classification
# ---------------------------------------------------------------------------

def classify_query(query: str, builder: HypergraphBuilder) -> list[str]:
    """Classify a query into categories that determine tier routing.

    Returns a list of classifications:
      - "identifier": query contains exact node name matches
      - "structural": query contains relationship verbs (calls, inherits, etc.)
      - "text_search": query terms are substrings of node names
      - "broad": query is very general / architectural
    """
    classifications: list[str] = []

    tokens = _tokenise_query(query)
    node_index = _build_node_index(builder)

    # Check for exact node matches
    has_exact = any(t in node_index for t in tokens)
    if has_exact:
        classifications.append("identifier")

    # Check for relationship verbs
    edge_types = infer_edge_types(query)
    if edge_types:
        classifications.append("structural")

    # Check for text matches (if no exact matches)
    if not has_exact:
        matched = get_matched_nodes(query, builder, max_results=5)
        if matched:
            classifications.append("text_search")

    # Broad/architectural queries
    broad_words = {
        "architecture", "structure", "overview", "components",
        "module", "modules", "design", "organization", "how does",
        "high level", "high-level", "explain",
    }
    query_lower = query.lower()
    if any(w in query_lower for w in broad_words):
        classifications.append("broad")

    # If nothing matched, it's a text search
    if not classifications:
        classifications.append("text_search")

    return classifications


# ---------------------------------------------------------------------------
# Directory collapse helpers
# ---------------------------------------------------------------------------

def _extract_dispatch_terms(query: str) -> list[str]:
    """Extract search terms from a query for directory name matching.

    Mirrors the stopword filtering in textsearch._extract_search_terms
    but kept separate to avoid circular imports.
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
        for part in re.split(r'[._]', tok):
            low = part.lower().strip()
            if low and low not in seen and low not in STOPWORDS and len(low) >= 3:
                seen.add(low)
                terms.append(low)
    return terms


# Priority penalty applied to test files so source code sorts first.
# Tests remain in results (they're useful for understanding usage patterns)
# but at lower priority than implementation files.
TEST_PATH_PRIORITY_PENALTY = 2


def _is_test_path(path: str) -> bool:
    """Detect whether a file path is a test file.

    Matches common Python project conventions:
      - Files under a 'tests/' or 'test/' directory
      - Files named test_*.py or *_test.py
      - conftest.py files
      - Files under directories like 'testing/', 'test_*/'

    Works on both individual file paths and collapsed directory paths
    (ending with '/').
    """
    # Normalize to forward slashes
    normalized = path.replace("\\", "/").lower()

    # Split into path segments
    parts = normalized.rstrip("/").split("/")

    for part in parts:
        # Directory named 'tests' or 'test'
        if part in ("tests", "test"):
            return True
        # Directory starting with 'test_' (e.g. test_migrations_plan/)
        if part.startswith("test_"):
            return True

    # Filename checks (only for non-directory paths)
    if not path.endswith("/") and parts:
        filename = parts[-1]
        if filename.startswith("test_") or filename.endswith("_test.py"):
            return True
        if filename in ("conftest.py", "testing.py"):
            return True

    return False


def _collapse_directories(
    files: list[FileSuggestion],
    query: str,
) -> list[FileSuggestion]:
    """Collapse files from the same directory into single directory entries.

    When 3+ files share a parent directory, they are replaced by one
    FileSuggestion whose path ends with '/' and whose reason shows the
    file count. Collapsed directories are ranked by name relevance and
    file count, matching the Tier 3 text search behaviour.

    Files whose path already ends with '/' (i.e. already collapsed by
    Tier 3) are passed through as-is and their directory is excluded
    from further collapsing.
    """
    from collections import defaultdict
    from pathlib import PurePosixPath

    DIR_COLLAPSE_THRESHOLD = 3

    def _normalize(p: str) -> str:
        """Normalize path separators to forward slashes for cross-platform consistency."""
        return p.replace("\\", "/")

    # Extract query terms for name-match scoring (same logic as textsearch)
    terms = _extract_dispatch_terms(query)

    # Separate already-collapsed directory entries from individual files
    already_collapsed: list[FileSuggestion] = []
    individual_files: list[FileSuggestion] = []
    already_collapsed_dirs: set[str] = set()

    for f in files:
        if f.path.endswith("/"):
            already_collapsed.append(f)
            # Track the dir so we don't re-collapse files under it
            already_collapsed_dirs.add(_normalize(f.path.rstrip("/")))
        else:
            individual_files.append(f)

    # Group individual files by parent directory
    dir_groups: dict[str, list[FileSuggestion]] = defaultdict(list)
    for f in individual_files:
        dir_path = _normalize(str(PurePosixPath(_normalize(f.path)).parent))
        # Don't group files whose directory was already collapsed by Tier 3
        if dir_path in already_collapsed_dirs:
            continue
        dir_groups[dir_path].append(f)

    # Collapse directories that meet the threshold
    collapsed: list[FileSuggestion] = []
    collapsed_dirs: set[str] = set()

    for dir_path, dir_files in dir_groups.items():
        if len(dir_files) >= DIR_COLLAPSE_THRESHOLD:
            # Gather symbols from all files in this directory
            all_symbols: list[str] = []
            best_priority = min(f.priority for f in dir_files)
            for f in dir_files:
                for s in f.symbols:
                    if s not in all_symbols:
                        all_symbols.append(s)
            unique_symbols = all_symbols[:10]

            # Rank the collapsed directory using name match + file count
            dir_name = PurePosixPath(dir_path).name.lower()
            name_bonus = any(term in dir_name for term in terms)
            file_count = len(dir_files)

            if name_bonus and file_count >= 5:
                priority = 1
            elif name_bonus or file_count >= 7:
                priority = 2
            else:
                # Use the best priority from the constituent files,
                # but floor at 3 so collapsed dirs without name match
                # don't outrank individually important files
                priority = max(best_priority, 3)

            collapsed.append(FileSuggestion(
                path=dir_path + "/",
                symbols=unique_symbols,
                reason=f"{file_count} files match",
                priority=priority,
            ))
            collapsed_dirs.add(dir_path)

    # Collect non-collapsed individual files
    remaining: list[FileSuggestion] = []
    for f in individual_files:
        dir_path = _normalize(str(PurePosixPath(_normalize(f.path)).parent))
        if dir_path not in collapsed_dirs and dir_path not in already_collapsed_dirs:
            remaining.append(f)

    # Combine: already-collapsed (from Tier 3) + newly collapsed + remaining individuals
    result = already_collapsed + collapsed + remaining

    # Apply test-path priority penalty so source files sort before tests
    for f in result:
        if _is_test_path(f.path):
            f.priority += TEST_PATH_PRIORITY_PENALTY

    result.sort(key=lambda f: f.priority)
    return result


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch(
    query: str,
    builder: HypergraphBuilder,
    *,
    depth: int = 2,
    max_results: int = 20,
    edge_types: list[str] | None = None,
    direction: str = "both",
) -> RetrievalPlan:
    """Route a query through the tiered retrieval system.

    Args:
        query: Natural language query or symbol name.
        builder: The hypergraph builder with all edges.
        depth: Max traversal depth for Tier 2.
        max_results: Max results for text search (Tier 3).
        edge_types: Override edge type filter (otherwise inferred from query).
        direction: Traversal direction for Tier 2.

    Returns:
        A merged RetrievalPlan combining results from all applicable tiers.
    """
    plan = RetrievalPlan(query=query)
    classifications = classify_query(query, builder)
    plan.classification = classifications

    # Infer edge types from query if not explicitly provided
    if edge_types is None:
        edge_types = infer_edge_types(query)

    hub_nodes = builder.get_hub_nodes()

    # Specificity filtering is only meaningful for graphs large enough
    # to have meaningful degree ratios. For tiny test/demo graphs, skip it.
    MIN_EDGES_FOR_SPECIFICITY = 50
    use_specificity = len(builder._incidence) >= MIN_EDGES_FOR_SPECIFICITY
    MIN_SEED_SPECIFICITY = 0.25

    # --- Tier 1: Exact lookup ---
    if "identifier" in classifications:
        t1_plan = lookup(query, builder, edge_types=edge_types)

        # For large graphs, filter Tier 1 results to remove files that are
        # only associated with low-specificity nodes (e.g. bare 'sql' node
        # matching 100 importers). Keep files that have at least one
        # high-specificity symbol.
        if use_specificity and t1_plan.related_symbols:
            low_spec_nodes: set[str] = set()
            for sym in t1_plan.related_symbols:
                score = _score_node_specificity(
                    sym.name, sym.name.rsplit(".", 1)[-1].lower(), builder,
                    edge_types=edge_types,
                )
                if score < MIN_SEED_SPECIFICITY:
                    low_spec_nodes.add(sym.name)

            if low_spec_nodes:
                # Remove files whose symbols are ALL low-specificity
                t1_plan.primary_files = [
                    f for f in t1_plan.primary_files
                    if not f.symbols or not all(s in low_spec_nodes for s in f.symbols)
                ]
                # Remove symbol relations for low-specificity nodes
                t1_plan.related_symbols = [
                    s for s in t1_plan.related_symbols
                    if s.name not in low_spec_nodes
                ]

        plan.merge(t1_plan)

        # --- Tier 2: Structural traversal from Tier 1 seeds ---
        if "structural" in classifications or not plan.is_empty():
            # Collect seed nodes, preferring those from source files
            # over test files. This keeps Tier 2 traversal focused on
            # implementation rather than fanning through test infrastructure.
            source_seeds: list[str] = []
            test_seeds: list[str] = []
            seen: set[str] = set()
            for sym in t1_plan.related_symbols:
                if sym.name not in seen:
                    seen.add(sym.name)
                    if _is_test_path(sym.file):
                        test_seeds.append(sym.name)
                    else:
                        source_seeds.append(sym.name)
            # Source seeds first, then test seeds as fallback
            seed_nodes = source_seeds + test_seeds

            if seed_nodes:
                if use_specificity:
                    # Rank seeds by specificity and filter low-value ones
                    scored_seeds = [
                        (node, _score_node_specificity(
                            node, node.rsplit(".", 1)[-1].lower(), builder,
                            edge_types=edge_types,
                        ))
                        for node in seed_nodes
                    ]
                    scored_seeds.sort(key=lambda x: x[1], reverse=True)
                    quality_seeds = [
                        node for node, score in scored_seeds
                        if score >= MIN_SEED_SPECIFICITY
                    ][:5]
                else:
                    quality_seeds = seed_nodes[:5]

                if quality_seeds:
                    t2_plan = traverse(
                        quality_seeds,
                        builder,
                        edge_types=edge_types,
                        depth=depth,
                        direction=direction,
                        hub_nodes=hub_nodes,
                    )
                    plan.merge(t2_plan)

    # --- Tier 3: Text search (if Tier 1 didn't find enough) ---
    if "text_search" in classifications or plan.is_empty():
        t3_plan = text_search(query, builder, max_results=max_results)
        plan.merge(t3_plan)

        # If we reached Tier 3 without Tier 1 results, expand text matches structurally
        if t3_plan.related_symbols and "identifier" not in classifications:
            # Prefer source-file symbols for structural expansion
            src_text = [s.name for s in t3_plan.related_symbols
                        if not _is_test_path(s.file)]
            test_text = [s.name for s in t3_plan.related_symbols
                         if _is_test_path(s.file)]
            text_nodes = (src_text + test_text)[:5]
            for node in text_nodes:
                t1_sub = lookup(node, builder, edge_types=edge_types)
                plan.merge(t1_sub)

            # And Tier 2 traversal with specificity filtering
            if text_nodes:
                if use_specificity:
                    scored_text = [
                        (node, _score_node_specificity(node, node.rsplit(".", 1)[-1].lower(), builder))
                        for node in text_nodes
                    ]
                    scored_text.sort(key=lambda x: x[1], reverse=True)
                    quality_text = [
                        node for node, score in scored_text
                        if score >= MIN_SEED_SPECIFICITY
                    ][:3]
                else:
                    quality_text = text_nodes[:3]

                if quality_text:
                    t2_sub = traverse(
                        quality_text,
                        builder,
                        edge_types=edge_types,
                        depth=min(depth, 1),  # shallow for text-search seeds
                        direction=direction,
                        hub_nodes=hub_nodes,
                    )
                    plan.merge(t2_sub)

    # --- Directory collapse on final output ---
    # When multiple tiers contribute files from the same directory,
    # collapse them into a single directory entry (same logic as Tier 3
    # text search). This prevents the agent from seeing 57 individual
    # files when 8 of them are in django/db/models/sql/ and could be
    # shown as one directory entry.
    plan.primary_files = _collapse_directories(plan.primary_files, query)

    return plan
