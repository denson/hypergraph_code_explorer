"""Tests for Tier 2 — Structural Traversal."""

from __future__ import annotations

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.traverse import (
    infer_edge_types,
    traverse,
)


def _make_edge(edge_id, sources, targets, edge_type="CALLS", source_path="test.py"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path=source_path, chunk_id=f"chunk_{edge_id}",
    )


def _build_call_chain() -> HypergraphBuilder:
    """Build a simple call chain: A -> B -> C -> D."""
    builder = HypergraphBuilder()
    builder.add_edge(_make_edge("e1", ["A"], ["B", "X"],
                                edge_type="CALLS", source_path="a.py"))
    builder.add_edge(_make_edge("e2", ["B"], ["C", "Y"],
                                edge_type="CALLS", source_path="b.py"))
    builder.add_edge(_make_edge("e3", ["C"], ["D"],
                                edge_type="CALLS", source_path="c.py"))
    # Also add an inheritance edge to test type filtering
    builder.add_edge(_make_edge("e4", ["B"], ["BaseB"],
                                edge_type="INHERITS", source_path="b.py"))
    return builder


def test_infer_edge_types_calls():
    assert infer_edge_types("what does Session call") == ["CALLS"]


def test_infer_edge_types_inherits():
    assert infer_edge_types("what inherits from AuthBase") == ["INHERITS"]


def test_infer_edge_types_imports():
    assert infer_edge_types("what imports session") == ["IMPORTS"]


def test_infer_edge_types_no_verb():
    assert infer_edge_types("how does authentication work") is None


def test_infer_edge_types_multiple_verbs():
    types = infer_edge_types("what calls and imports Session")
    assert "CALLS" in types
    assert "IMPORTS" in types


def test_traverse_depth_1():
    builder = _build_call_chain()
    plan = traverse(["A"], builder, depth=1, direction="forward")
    assert not plan.is_empty()
    # At depth 1, should reach B and X but not C
    all_targets = []
    for sym in plan.related_symbols:
        all_targets.extend(sym.targets)
    assert "B" in all_targets
    assert "D" not in all_targets  # too deep


def test_traverse_depth_2():
    builder = _build_call_chain()
    plan = traverse(["A"], builder, depth=2, direction="forward")
    all_targets = []
    for sym in plan.related_symbols:
        all_targets.extend(sym.targets)
    assert "B" in all_targets
    assert "C" in all_targets  # reachable at depth 2


def test_traverse_edge_type_filter():
    builder = _build_call_chain()
    # Only follow CALLS, not INHERITS
    plan = traverse(["B"], builder, edge_types=["CALLS"], depth=1, direction="forward")
    for sym in plan.related_symbols:
        assert sym.edge_type == "CALLS"


def test_traverse_backward():
    builder = _build_call_chain()
    # Going backward from C should find B
    plan = traverse(["C"], builder, depth=1, direction="backward")
    all_sources = []
    for sym in plan.related_symbols:
        all_sources.extend(sym.targets)  # "targets" in backward = sources
    assert "B" in all_sources


def test_traverse_returns_files():
    builder = _build_call_chain()
    plan = traverse(["A"], builder, depth=2, direction="forward")
    paths = {f.path for f in plan.primary_files}
    assert "a.py" in paths


def test_traverse_structural_context():
    builder = _build_call_chain()
    plan = traverse(["A"], builder, depth=1, direction="forward")
    assert plan.structural_context  # should have context lines


def test_traverse_empty_seed():
    builder = _build_call_chain()
    plan = traverse([], builder, depth=1)
    assert plan.is_empty()


def test_traverse_nonexistent_seed():
    builder = _build_call_chain()
    plan = traverse(["Nonexistent"], builder, depth=1)
    assert plan.is_empty()


def test_traverse_hub_filtering():
    """Hub nodes should be skipped during traversal."""
    builder = HypergraphBuilder()
    # Create a hub node that appears in many edges
    for i in range(50):
        builder.add_edge(_make_edge(
            f"hub_e{i}", ["hub_node"], [f"target_{i}", f"other_{i}"],
            edge_type="CALLS",
        ))
    # And a normal edge from our seed through the hub
    builder.add_edge(_make_edge("seed_e", ["Seed"], ["hub_node", "direct_target"],
                                edge_type="CALLS"))

    hub_nodes = builder.get_hub_nodes()
    assert "hub_node" in hub_nodes

    plan = traverse(["Seed"], builder, depth=2, direction="forward", hub_nodes=hub_nodes)
    # Should reach direct_target but NOT the 50 hub targets
    all_targets = []
    for sym in plan.related_symbols:
        all_targets.extend(sym.targets)
    assert "direct_target" in all_targets
    # hub_node's targets should not be traversed to (they're filtered)
    assert "target_0" not in all_targets
