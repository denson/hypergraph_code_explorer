"""Tests for Tier 1 — Exact Lookup."""

from __future__ import annotations

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.lookup import lookup, _tokenise_query


def _make_edge(edge_id, sources, targets, edge_type="CALLS", source_path="test.py"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path=source_path, chunk_id=f"chunk_{edge_id}",
    )


def _build_graph() -> HypergraphBuilder:
    builder = HypergraphBuilder()
    builder.add_edge(_make_edge("e1", ["sessions.Session"], ["sessions.Session.send", "sessions.Session.get"],
                                edge_type="DEFINES", source_path="sessions.py"))
    builder.add_edge(_make_edge("e2", ["sessions.Session.send"], ["get_adapter", "adapter.send"],
                                edge_type="CALLS", source_path="sessions.py"))
    builder.add_edge(_make_edge("e3", ["adapters"], ["HTTPAdapter"],
                                edge_type="IMPORTS", source_path="sessions.py"))
    builder.add_edge(_make_edge("e4", ["sessions.Session"], ["SessionRedirectMixin"],
                                edge_type="INHERITS", source_path="sessions.py"))
    builder.add_edge(_make_edge("e5", ["auth.AuthBase"], ["auth.HTTPBasicAuth", "auth.HTTPDigestAuth"],
                                edge_type="DEFINES", source_path="auth.py"))
    return builder


def test_tokenise_query_splits_dotted():
    tokens = _tokenise_query("Session.send")
    assert "session.send" in tokens
    assert "session" in tokens
    assert "send" in tokens


def test_tokenise_query_splits_underscores():
    tokens = _tokenise_query("get_adapter")
    # "get" is a stopword and filtered out
    assert "adapter" in tokens
    # Non-stopword parts are kept
    tokens2 = _tokenise_query("send_request")
    assert "send" in tokens2
    assert "request" in tokens2


def test_lookup_exact_match():
    builder = _build_graph()
    plan = lookup("sessions.Session.send", builder)
    assert not plan.is_empty()
    assert 1 in plan.tiers_used
    # Should find the CALLS edge from Session.send
    call_rels = [s for s in plan.related_symbols if s.edge_type == "CALLS"]
    assert len(call_rels) > 0


def test_lookup_case_insensitive():
    builder = _build_graph()
    plan = lookup("session.send", builder)
    # Should match "sessions.Session.send" case-insensitively
    # (the token "session" matches the node via the index)
    assert not plan.is_empty()


def test_lookup_returns_files():
    builder = _build_graph()
    plan = lookup("sessions.Session", builder)
    paths = {f.path for f in plan.primary_files}
    assert "sessions.py" in paths


def test_lookup_returns_grep_suggestions():
    builder = _build_graph()
    plan = lookup("sessions.Session.send", builder)
    patterns = {g.pattern for g in plan.grep_suggestions}
    assert "send" in patterns


def test_lookup_no_match_returns_empty():
    builder = _build_graph()
    plan = lookup("NonexistentSymbol", builder)
    assert plan.is_empty()


def test_lookup_with_edge_type_filter():
    builder = _build_graph()
    plan = lookup("sessions.Session", builder, edge_types=["CALLS"])
    # Should only return CALLS edges, not DEFINES or INHERITS
    for sym in plan.related_symbols:
        assert sym.edge_type == "CALLS"


def test_lookup_structural_context_lists_nodes():
    builder = _build_graph()
    plan = lookup("sessions.Session", builder)
    assert "sessions.Session" in plan.structural_context


def test_lookup_multiple_tokens():
    builder = _build_graph()
    plan = lookup("Session send", builder)
    # Should match both "sessions.Session" and "sessions.Session.send"
    # (via individual tokens)
    assert not plan.is_empty()
    matched_names = {s.name for s in plan.related_symbols}
    # At least one of these should be found
    assert len(matched_names) > 0


# --- Node specificity scoring ---

def test_score_node_specificity_penalises_high_relative_degree():
    """Nodes touching a large fraction of edges should score lower than rare ones."""
    builder = HypergraphBuilder()
    # 'sql' touches 200 out of 201 total edges (~99%) — extremely generic
    for i in range(200):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"e{i}", relation=f"imports sql {i}",
            edge_type="IMPORTS", sources=[f"file{i}"], targets=["sql"],
            source_path=f"f{i}.py",
        ))
    # 'QuerySet' touches 1 out of 201 edges (~0.5%) — very specific
    builder.add_edge(HyperedgeRecord(
        edge_id="e_qs", relation="defines QuerySet",
        edge_type="DEFINES", sources=["query"], targets=["QuerySet"],
        source_path="query.py",
    ))

    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity
    score_sql = _score_node_specificity("sql", "sql", builder)
    score_qs = _score_node_specificity("QuerySet", "queryset", builder)
    assert score_qs > score_sql, f"QuerySet ({score_qs}) should score higher than sql ({score_sql})"


def test_score_node_specificity_scales_with_graph_size():
    """The same degree should score differently depending on total graph size."""
    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity

    # Small graph: 20 edges total, 'sql' has 5 edges (25%)
    small = HypergraphBuilder()
    for i in range(5):
        small.add_edge(HyperedgeRecord(
            edge_id=f"s_{i}", relation="imports sql",
            edge_type="IMPORTS", sources=[f"f{i}"], targets=["sql"],
            source_path=f"f{i}.py",
        ))
    for i in range(15):
        small.add_edge(HyperedgeRecord(
            edge_id=f"o_{i}", relation="other",
            edge_type="DEFINES", sources=[f"a{i}"], targets=[f"b{i}"],
            source_path=f"a{i}.py",
        ))

    # Large graph: 2000 edges total, 'sql' has 5 edges (0.25%)
    large = HypergraphBuilder()
    for i in range(5):
        large.add_edge(HyperedgeRecord(
            edge_id=f"s_{i}", relation="imports sql",
            edge_type="IMPORTS", sources=[f"f{i}"], targets=["sql"],
            source_path=f"f{i}.py",
        ))
    for i in range(1995):
        large.add_edge(HyperedgeRecord(
            edge_id=f"o_{i}", relation="other",
            edge_type="DEFINES", sources=[f"a{i}"], targets=[f"b{i}"],
            source_path=f"a{i}.py",
        ))

    score_small = _score_node_specificity("sql", "sql", small)
    score_large = _score_node_specificity("sql", "sql", large)
    assert score_large > score_small, (
        f"Same node with 5 edges should score higher in a 2000-edge graph "
        f"({score_large}) than in a 20-edge graph ({score_small})"
    )


def test_score_node_specificity_short_token_low_degree_no_penalty():
    """Short tokens should NOT be penalised if the node has low relative degree."""
    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity

    builder = HypergraphBuilder()
    # 'Q' has 3 edges in a graph with 1000 edges — very specific (0.3%)
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"q_{i}", relation="uses Q",
            edge_type="CALLS", sources=[f"view{i}"], targets=["Q"],
            source_path=f"view{i}.py",
        ))
    for i in range(997):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"o_{i}", relation="other",
            edge_type="DEFINES", sources=[f"a{i}"], targets=[f"b{i}"],
            source_path=f"a{i}.py",
        ))

    score_q = _score_node_specificity("Q", "q", builder)
    # Should not be heavily penalised — degree ratio is ~0.3%, which is
    # in the mild penalty band (0.002-0.005) giving length_factor=0.7.
    # Still well above the MIN_SEED_SPECIFICITY threshold of 0.25.
    assert score_q >= 0.5, (
        f"Short token 'q' with low relative degree should score >= 0.5 "
        f"but got {score_q}"
    )


def test_score_node_specificity_qualified_bonus():
    """Qualified names like 'django.db.models.sql' should score higher than bare 'sql'."""
    builder = HypergraphBuilder()
    builder.add_edge(HyperedgeRecord(
        edge_id="e1", relation="test",
        edge_type="IMPORTS", sources=["a"], targets=["sql"],
        source_path="a.py",
    ))
    builder.add_edge(HyperedgeRecord(
        edge_id="e2", relation="test",
        edge_type="IMPORTS", sources=["b"], targets=["django.db.models.sql"],
        source_path="b.py",
    ))

    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity
    score_bare = _score_node_specificity("sql", "sql", builder)
    score_qualified = _score_node_specificity("django.db.models.sql", "sql", builder)
    assert score_qualified > score_bare
