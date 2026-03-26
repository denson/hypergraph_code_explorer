"""
Analyze Command
===============
General-purpose tour-guided codebase analysis.

Takes a plain-English question, decomposes it into multiple HCE queries,
assembles a tour from the merged results, and generates visualization +
analysis prompt.  No LLM calls — all classification and query planning
is rule-based.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .memory_tours import MemoryTour, MemoryTourStep
from .retrieval.plan import RetrievalPlan

if TYPE_CHECKING:
    from .api import HypergraphSession


# ---------------------------------------------------------------------------
# Strategy classification (rule-based, no LLM)
# ---------------------------------------------------------------------------

STRATEGY_PATTERNS: dict[str, list[str]] = {
    "blast-radius": [
        r"\bimpact\b", r"\bblast.?radius\b", r"\bdepends?\s+on\b",
        r"\bwho\s+calls\b", r"\bwhat\s+calls\b", r"\bcallers?\b",
        r"\bwhat\s+would\s+break\b", r"\bchange\b", r"\bmodify\b",
        r"\baffect\b", r"\bripple\b",
    ],
    "inheritance": [
        r"\bhierarch", r"\bsubclass", r"\binherit", r"\bimplement",
        r"\bextend", r"\bbase\s+class", r"\bmixin", r"\boverrid",
        r"\bchild\s+class", r"\bparent\s+class", r"\btype\s+hierarchy",
        r"\bclass\s+tree",
    ],
    "data-flow": [
        r"\btrace\b", r"\bflow\b", r"\bpath\b", r"\bpipeline\b",
        r"\bexecution\b", r"\blifecycle\b", r"\breach\b",
        r"\bpass\w*\s+through\b", r"\bchain\b", r"\bsequence\b",
        r"\bstep.?by.?step\b", r"\bhow\s+does\s+\w+\s+(get|reach|flow|pass)",
    ],
    "exception-flow": [
        r"\bexception", r"\berror\s+handl", r"\braise[sd]?\b",
        r"\bcatch\b", r"\btry\b", r"\bexcept\b", r"\bfail\b",
        r"\brecov", r"\berror\s+propagat",
    ],
    "api-surface": [
        r"\bpublic\b", r"\bapi\b", r"\binterface\b", r"\bexport",
        r"\bmodule\s+surface\b", r"\bexposed\b", r"\bendpoint",
    ],
    "cross-cutting": [
        r"\beverywhere\b", r"\ball\s+places\b", r"\bpattern\b",
        r"\bconvention\b", r"\bcross.?cutting\b", r"\bacross\b",
        r"\bconsistent", r"\bcommon\s+pattern",
    ],
}


def classify_analysis(question: str) -> list[str]:
    """Classify question into analysis strategies.

    Returns list of strategy names. If no patterns match, returns
    ``["exploration"]``.
    """
    q_lower = question.lower()
    matched: list[str] = []
    for strategy, patterns in STRATEGY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, q_lower):
                matched.append(strategy)
                break
    return matched or ["exploration"]


# ---------------------------------------------------------------------------
# Seed term extraction
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "how", "does", "what", "why", "when", "where", "which",
    "the", "and", "for", "that", "this", "with", "from", "into",
    "use", "uses", "used", "get", "set", "has", "have", "can",
    "will", "would", "should", "each", "some", "any", "are",
    "was", "were", "been", "being", "about", "work", "works",
    "all", "its", "not", "but", "they", "them", "their", "there",
    "class", "method", "function", "module", "file", "code",
    "through", "between", "reach", "call", "calls",
}


def extract_seed_terms(question: str) -> list[str]:
    """Extract likely symbol names and search terms from the question.

    More aggressive than dispatch._extract_dispatch_terms — also detects
    CamelCase tokens and dot-paths (e.g. ``Pipeline.fit``).
    """
    terms: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        low = tok.lower()
        if low not in seen and low not in _STOPWORDS and len(tok) >= 3:
            seen.add(low)
            terms.append(tok)

    # Detect dot-paths first (e.g. Pipeline.fit, forms.ValidationError)
    for m in re.finditer(r'[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+', question):
        tok = m.group()
        _add(tok)
        for part in tok.split('.'):
            _add(part)

    # Detect CamelCase words
    for m in re.finditer(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', question):
        _add(m.group())

    # Remaining tokens: split on whitespace/punctuation, filter stopwords
    raw = re.split(r'[\s,;:!?(){}\[\]"\'`/\\]+', question)
    for tok in raw:
        for part in re.split(r'[._]', tok):
            low = part.lower().strip()
            if low and low not in seen and low not in _STOPWORDS and len(low) >= 3:
                seen.add(low)
                terms.append(part)

    return terms


# ---------------------------------------------------------------------------
# Multi-query planner
# ---------------------------------------------------------------------------

def plan_queries(
    question: str,
    strategies: list[str],
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 2,
) -> RetrievalPlan:
    """Execute multi-query plan for the given strategies.

    Runs multiple lookup/search/query calls, merges all results into a
    single RetrievalPlan.
    """
    combined = RetrievalPlan(query=question)

    for strategy in strategies:
        if strategy == "blast-radius":
            _plan_blast_radius(combined, seed_terms, session, depth=depth)
        elif strategy == "inheritance":
            _plan_inheritance(combined, seed_terms, session, depth=depth)
        elif strategy == "data-flow":
            _plan_data_flow(combined, seed_terms, session, depth=depth)
        elif strategy == "exception-flow":
            _plan_exception_flow(combined, seed_terms, session, depth=depth)
        elif strategy == "api-surface":
            _plan_api_surface(combined, seed_terms, session, depth=depth)
        elif strategy == "cross-cutting":
            _plan_cross_cutting(combined, seed_terms, session, depth=depth)
        else:  # exploration
            _plan_exploration(combined, question, seed_terms, session, depth=depth)

    return combined


def _plan_blast_radius(
    plan: RetrievalPlan,
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 2,
) -> None:
    for term in seed_terms[:5]:
        p = session.lookup(term, direction="both", depth=depth)
        if not p.is_empty():
            plan.merge(p)
        p_raises = session.lookup(term, edge_types=["RAISES"], direction="both", depth=depth)
        if not p_raises.is_empty():
            plan.merge(p_raises)
        p_inherits = session.lookup(term, edge_types=["INHERITS"], direction="both", depth=depth)
        if not p_inherits.is_empty():
            plan.merge(p_inherits)


def _plan_inheritance(
    plan: RetrievalPlan,
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 3,
) -> None:
    for term in seed_terms[:5]:
        p = session.lookup(term, edge_types=["INHERITS"], direction="both", depth=depth)
        if not p.is_empty():
            plan.merge(p)
        p_search = session.search(term, max_results=10)
        if not p_search.is_empty():
            plan.merge(p_search)


def _plan_data_flow(
    plan: RetrievalPlan,
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 2,
) -> None:
    for term in seed_terms[:5]:
        p = session.lookup(term, edge_types=["CALLS"], direction="both", depth=depth)
        if not p.is_empty():
            plan.merge(p)
    # Also search for terms to find related entry points
    for term in seed_terms[:3]:
        p_search = session.search(term, max_results=10)
        if not p_search.is_empty():
            plan.merge(p_search)


def _plan_exception_flow(
    plan: RetrievalPlan,
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 2,
) -> None:
    for term in seed_terms[:5]:
        p = session.lookup(term, edge_types=["RAISES"], direction="both", depth=depth)
        if not p.is_empty():
            plan.merge(p)
    # Also search for error/exception terms
    error_terms = [t for t in seed_terms if any(
        kw in t.lower() for kw in ("error", "exception", "fail", "invalid")
    )]
    for term in (error_terms or seed_terms)[:3]:
        p_search = session.search(term, max_results=15)
        if not p_search.is_empty():
            plan.merge(p_search)


def _plan_api_surface(
    plan: RetrievalPlan,
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 1,
) -> None:
    # Get overview for hub nodes
    overview = session.overview(top=20)
    if overview.get("key_symbols"):
        for sym in overview["key_symbols"][:10]:
            p = session.lookup(sym["name"], edge_types=["CALLS"], direction="both", depth=depth)
            if not p.is_empty():
                plan.merge(p)
    # Search specific terms
    for term in seed_terms[:5]:
        p_search = session.search(term, max_results=10)
        if not p_search.is_empty():
            plan.merge(p_search)


def _plan_cross_cutting(
    plan: RetrievalPlan,
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 1,
) -> None:
    for term in seed_terms[:5]:
        p_search = session.search(term, max_results=20)
        if not p_search.is_empty():
            plan.merge(p_search)
            # Expand top matches with callers
            for sym in p_search.related_symbols[:5]:
                p_callers = session.lookup(
                    sym.name, edge_types=["CALLS"], direction="both", depth=depth,
                )
                if not p_callers.is_empty():
                    plan.merge(p_callers)


def _plan_exploration(
    plan: RetrievalPlan,
    question: str,
    seed_terms: list[str],
    session: HypergraphSession,
    *,
    depth: int = 2,
) -> None:
    # Full dispatch query
    p = session.query(question, depth=depth, max_results=20)
    if not p.is_empty():
        plan.merge(p)
    # Search individual terms
    for term in seed_terms[:5]:
        p_search = session.search(term, max_results=10)
        if not p_search.is_empty():
            plan.merge(p_search)
    # Expand top results
    for sym in plan.related_symbols[:5]:
        p_expand = session.lookup(sym.name, depth=1)
        if not p_expand.is_empty():
            plan.merge(p_expand)


# ---------------------------------------------------------------------------
# Context query templates per strategy
# ---------------------------------------------------------------------------

CONTEXT_TEMPLATES: dict[str, str] = {
    "blast-radius": "How does {node} interact with {seed}? Would it be affected by: {question}",
    "inheritance": "What does {node} inherit or override? How does its behavior differ from the base class? Relevant to: {question}",
    "data-flow": "What transformation or validation does {node} apply? How does data flow through it? Context: {question}",
    "exception-flow": "Does {node} raise, catch, or propagate exceptions? What error conditions does it handle? Context: {question}",
    "api-surface": "Is {node} part of the public API? What contract does it expose? Context: {question}",
    "cross-cutting": "How does {node} implement this pattern? Is it consistent with other locations? Context: {question}",
    "exploration": "How does {node} relate to the question: {question}",
}


# ---------------------------------------------------------------------------
# Tour assembly
# ---------------------------------------------------------------------------

def build_analysis_tour(
    question: str,
    plan: RetrievalPlan,
    strategies: list[str],
    seed_terms: list[str],
    *,
    max_tour_steps: int = 200,
    tags: list[str] | None = None,
) -> MemoryTour:
    """Assemble a MemoryTour from the merged plan.

    Orders steps by module grouping + importance, generates strategy-appropriate
    context_query per step, and generates narrative text per step.
    """
    steps: list[MemoryTourStep] = []
    keywords: list[str] = []
    seen_nodes: set[str] = set()

    # Collect candidate steps from related_symbols
    candidates: list[dict] = []
    for sym in plan.related_symbols:
        if sym.name in seen_nodes:
            continue
        seen_nodes.add(sym.name)
        keywords.append(sym.name)

        text = sym.name
        if sym.relationship:
            text += f" [{sym.relationship}]"
        if sym.targets:
            text += " -> " + ", ".join(sym.targets[:5])

        candidates.append({
            "node": sym.name,
            "text": text,
            "file": sym.file,
            "edge_type": sym.edge_type,
        })

    # Also pull in primary file symbols not already covered
    for fs in plan.primary_files:
        for sym_name in fs.symbols:
            if sym_name not in seen_nodes:
                seen_nodes.add(sym_name)
                keywords.append(sym_name)
                reason = fs.reason or f"in {fs.path}"
                candidates.append({
                    "node": sym_name,
                    "text": f"{sym_name} — {reason}",
                    "file": fs.path,
                    "edge_type": "",
                })

    # Priority scoring: seed term matches, hub nodes, source > test
    seed_set = {t.lower() for t in seed_terms}

    def step_priority(c: dict) -> tuple:
        node_lower = c["node"].lower()
        # How many seed terms match this node?
        seed_matches = sum(1 for s in seed_set if s in node_lower)
        # Penalize test files
        is_test = 1 if _is_test_path(c.get("file", "")) else 0
        # Group by file for locality
        file_key = c.get("file", "")
        return (-seed_matches, is_test, file_key, c["node"])

    candidates.sort(key=step_priority)

    # Cap at max_tour_steps
    candidates = candidates[:max_tour_steps]

    # Pick the primary strategy for context queries
    primary_strategy = strategies[0] if strategies else "exploration"
    template = CONTEXT_TEMPLATES.get(primary_strategy, CONTEXT_TEMPLATES["exploration"])
    seed_label = ", ".join(seed_terms[:3]) if seed_terms else question[:50]

    for c in candidates:
        context_query = template.format(
            node=c["node"],
            seed=seed_label,
            question=question,
        )
        steps.append(MemoryTourStep(
            node=c["node"],
            text=c["text"],
            file=c.get("file", ""),
            edge_type=c.get("edge_type", ""),
            context_query=context_query,
        ))

    tour_tags = list(strategies) + (tags or [])
    tour_name = f"Analysis: {question[:80]}"

    summary_parts = [f"Question: {question}"]
    summary_parts.append(f"Strategies: {', '.join(strategies)}")
    summary_parts.append(f"{len(steps)} steps, {len(plan.primary_files)} files")

    return MemoryTour(
        id="",
        name=tour_name,
        summary="; ".join(summary_parts),
        keywords=keywords,
        steps=steps,
        tags=tour_tags,
        created_from_query=question,
        promoted=False,
    )


def _is_test_path(path: str) -> bool:
    """Detect test file paths."""
    normalized = path.replace("\\", "/").lower()
    parts = normalized.rstrip("/").split("/")
    for part in parts:
        if part in ("tests", "test", "testing"):
            return True
        if part.startswith("test_") or part.endswith("_test"):
            return True
    basename = parts[-1] if parts else ""
    if basename.startswith("test_") or basename.endswith("_test.py"):
        return True
    if basename == "conftest.py":
        return True
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze(
    question: str,
    session: HypergraphSession,
    *,
    depth: int = 2,
    max_tour_steps: int = 200,
    tags: list[str] | None = None,
    strategies: list[str] | None = None,
) -> MemoryTour:
    """Main entry point. Orchestrates classify -> plan -> build -> persist.

    Args:
        question: Plain-English question about the codebase.
        session: Loaded HypergraphSession with indexed graph.
        depth: Traversal depth for structural queries.
        max_tour_steps: Maximum tour steps.
        tags: Additional tags for the tour.
        strategies: Override auto-detected strategies.

    Returns:
        Persisted MemoryTour.
    """
    if not strategies:
        strategies = classify_analysis(question)

    seed_terms = extract_seed_terms(question)

    plan = plan_queries(question, strategies, seed_terms, session, depth=depth)

    if plan.is_empty():
        # Nothing found — return a minimal tour with a helpful message
        tour = MemoryTour(
            id="",
            name=f"Analysis: {question[:80]}",
            summary=f"No results found for: {question}",
            keywords=seed_terms,
            steps=[],
            tags=strategies + (tags or []),
            created_from_query=question,
        )
        store = session._get_tour_store()
        store.add(tour)
        return tour

    tour = build_analysis_tour(
        question, plan, strategies, seed_terms,
        max_tour_steps=max_tour_steps,
        tags=tags,
    )

    # Persist
    store = session._get_tour_store()
    store.add(tour)
    return tour
