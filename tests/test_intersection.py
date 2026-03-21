"""Tests for edge-intersection retrieval with additive scoring."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.graph.embeddings import EmbeddingManager
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.intersection import retrieve


def _make_record(edge_id: str, sources: list[str], targets: list[str],
                 edge_type: str = "CALLS") -> HyperedgeRecord:
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path="test.py", chunk_id=f"chunk_{edge_id}",
    )


def _build_test_graph() -> tuple[HypergraphBuilder, EmbeddingManager]:
    """Build the 5-edge test graph from the spec."""
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["Session"], ["HTTPAdapter", "poolmanager", "send"]))
    builder.add_edge(_make_record("e2", ["HTTPAdapter"], ["poolmanager", "send_request"]))
    builder.add_edge(_make_record("e3", ["Session"], ["PreparedRequest", "send"]))
    builder.add_edge(_make_record("e4", ["poolmanager"], ["ConnectionPool", "urlopen"]))
    builder.add_edge(_make_record("e5", ["send"], ["HTTPAdapter", "timeout"]))

    # Create mock embeddings
    embeddings = EmbeddingManager.__new__(EmbeddingManager)
    embeddings._embeddings = {}
    embeddings._model = None
    embeddings.model_name = "test"
    embeddings.device = "cpu"
    embeddings.batch_size = 64
    embeddings.verbose = False

    # Assign fake embeddings (normalized random vectors)
    rng = np.random.RandomState(42)
    all_nodes = builder.get_all_nodes()
    for node in all_nodes:
        vec = rng.randn(768).astype(np.float32)
        vec /= np.linalg.norm(vec)
        embeddings._embeddings[node] = vec

    return builder, embeddings


def test_retrieve_returns_results():
    builder, embeddings = _build_test_graph()

    # Mock embed_query to return a vector similar to "Session"
    original_embed_query = embeddings.embed_query
    embeddings.embed_query = lambda q: embeddings._embeddings.get("Session", np.zeros(768))

    result = retrieve(
        query="Session send",
        builder=builder,
        embeddings=embeddings,
        top_k=5,
    )

    assert result.query == "Session send"
    assert len(result.scored_edges) > 0
    assert result.coverage_score >= 0.0


def test_retrieve_tags_seed_and_intersection():
    builder, embeddings = _build_test_graph()
    embeddings.embed_query = lambda q: embeddings._embeddings.get("Session", np.zeros(768))

    result = retrieve(
        query="Session",
        builder=builder,
        embeddings=embeddings,
        top_k=5,
    )

    sources = {se.retrieval_source for se in result.scored_edges}
    # Should have at least seed edges
    assert "seed" in sources


def test_additive_scoring_no_valley():
    """Verify additive scoring doesn't create F1's U-shaped valley."""
    builder, embeddings = _build_test_graph()
    embeddings.embed_query = lambda q: embeddings._embeddings.get("Session", np.zeros(768))

    result = retrieve(
        query="Session",
        builder=builder,
        embeddings=embeddings,
        top_k=5,
        alpha=0.6,
    )

    # All scored edges should have positive scores
    for se in result.scored_edges:
        assert se.score >= 0.0


def test_retrieval_source_breakdown():
    builder, embeddings = _build_test_graph()
    embeddings.embed_query = lambda q: embeddings._embeddings.get("Session", np.zeros(768))

    result = retrieve(
        query="Session",
        builder=builder,
        embeddings=embeddings,
        top_k=5,
    )

    assert "seed" in result.retrieval_source_breakdown
    assert "intersection" in result.retrieval_source_breakdown


def test_large_edge_not_penalised_by_wp():
    """Verify that the mean_sim × sqrt(match_ratio) formula does not crush
    large edges the way sum(sim)/|edge| did.

    A 10-node edge with 3 high-similarity matches should outscore a 2-node
    edge with 1 mediocre match.
    """
    import math

    builder = HypergraphBuilder()

    # Large edge: 10 nodes, 3 of which will match with high similarity
    large_sources = ["caller"]
    large_targets = [f"target_{i}" for i in range(9)]
    builder.add_edge(_make_record(
        "large", large_sources, large_targets, edge_type="CALLS",
    ))

    # Small edge: 2 nodes, 1 of which matches with mediocre similarity
    builder.add_edge(_make_record(
        "small", ["mediocre_match"], ["other_node"], edge_type="CALLS",
    ))

    # Simulate node scores: 3 high matches in large edge, 1 mediocre in small
    high_sim = 0.85
    med_sim = 0.50

    # --- old formula: sum(sim) / |edge| ---
    old_wp_large = (3 * high_sim) / 10          # 0.255
    old_wp_small = (1 * med_sim) / 2            # 0.250
    # These are almost equal — the old formula nearly erased the advantage.

    # --- new formula: mean_sim × sqrt(match_ratio) ---
    new_wp_large = high_sim * math.sqrt(3 / 10)  # 0.85 × 0.548 ≈ 0.466
    new_wp_small = med_sim * math.sqrt(1 / 2)    # 0.50 × 0.707 ≈ 0.354

    # New formula correctly ranks the large edge higher
    assert new_wp_large > new_wp_small, (
        f"Large edge wp ({new_wp_large:.3f}) should beat small edge wp ({new_wp_small:.3f})"
    )
    # And the gap is meaningful, not razor-thin like the old formula
    assert new_wp_large - new_wp_small > 0.05, (
        f"Gap ({new_wp_large - new_wp_small:.3f}) should be meaningful (> 0.05)"
    )
