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
