"""Tests for coverage evaluation."""

from __future__ import annotations

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.coverage import evaluate_coverage


def _make_record(edge_id: str, sources: list[str], targets: list[str]) -> HyperedgeRecord:
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type="CALLS", sources=sources, targets=targets,
        source_path="test.py", chunk_id=f"chunk_{edge_id}",
    )


def test_full_coverage():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B", "C"]))

    result = evaluate_coverage(
        retrieved_edge_ids=["e1"],
        seed_node_ids=["A", "B"],
        builder=builder,
    )

    assert result.coverage_score == 1.0
    assert len(result.uncovered_nodes) == 0


def test_partial_coverage():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B"]))

    result = evaluate_coverage(
        retrieved_edge_ids=["e1"],
        seed_node_ids=["A", "B", "C"],
        builder=builder,
    )

    assert result.coverage_score < 1.0
    assert "C" in result.uncovered_nodes


def test_frontier_nodes_detected():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B", "C"]))
    builder.add_edge(_make_record("e2", ["B"], ["D", "E"]))

    result = evaluate_coverage(
        retrieved_edge_ids=["e1"],
        seed_node_ids=["A", "B"],
        builder=builder,
    )

    # D and E should be frontier nodes (in e2 which intersects e1 via B)
    frontier_names = [f["node"] for f in result.frontier_nodes]
    assert "D" in frontier_names or "E" in frontier_names


def test_intersection_density():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B", "C"]))
    builder.add_edge(_make_record("e2", ["B"], ["D"]))

    result = evaluate_coverage(
        retrieved_edge_ids=["e1", "e2"],
        seed_node_ids=["A", "B"],
        builder=builder,
    )

    # e1 and e2 share "B", so density should be 1.0
    assert result.intersection_density == 1.0


def test_empty_retrieval():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B"]))

    result = evaluate_coverage(
        retrieved_edge_ids=[],
        seed_node_ids=["A"],
        builder=builder,
    )

    assert result.coverage_score == 0.0
