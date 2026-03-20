"""Tests for edge-BFS pathfinder."""

from __future__ import annotations

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.pathfinder import find_paths


def _make_record(edge_id: str, sources: list[str], targets: list[str],
                 edge_type: str = "CALLS") -> HyperedgeRecord:
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path="test.py", chunk_id=f"chunk_{edge_id}",
    )


def _build_test_graph() -> HypergraphBuilder:
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["Session"], ["HTTPAdapter", "poolmanager", "send"]))
    builder.add_edge(_make_record("e2", ["HTTPAdapter"], ["poolmanager", "send_request"]))
    builder.add_edge(_make_record("e3", ["Session"], ["PreparedRequest", "send"]))
    builder.add_edge(_make_record("e4", ["poolmanager"], ["ConnectionPool", "urlopen"]))
    builder.add_edge(_make_record("e5", ["send"], ["HTTPAdapter", "timeout"]))
    return builder


def test_direct_connection():
    """Nodes in the same edge should return a direct path."""
    builder = _build_test_graph()
    paths = find_paths("Session", "HTTPAdapter", builder)
    assert len(paths) > 0
    # e1 contains both Session and HTTPAdapter
    assert any(len(p.edges) == 1 for p in paths)


def test_multi_hop_path():
    """PreparedRequest to ConnectionPool requires traversal."""
    builder = _build_test_graph()
    paths = find_paths("PreparedRequest", "ConnectionPool", builder)
    assert len(paths) > 0
    # Should require at least 2 edges
    for path in paths:
        assert len(path.edges) >= 2


def test_path_has_intersection_nodes():
    """Multi-hop paths should report intersection nodes."""
    builder = _build_test_graph()
    paths = find_paths("PreparedRequest", "ConnectionPool", builder)
    assert len(paths) > 0

    for path in paths:
        for hop in path.hops:
            assert len(hop.intersection_nodes) > 0
            assert hop.from_edge != hop.to_edge


def test_no_path_for_disconnected_nodes():
    """Disconnected nodes should return empty paths."""
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B"]))
    builder.add_edge(_make_record("e2", ["C"], ["D"]))

    paths = find_paths("A", "C", builder)
    assert len(paths) == 0


def test_k_paths_limit():
    builder = _build_test_graph()
    paths = find_paths("Session", "HTTPAdapter", builder, k_paths=1)
    assert len(paths) <= 1
