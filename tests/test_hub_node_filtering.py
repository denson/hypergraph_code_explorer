"""Tests for hub node filtering and IDF weighting."""

from __future__ import annotations

import math

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord


def _make_edge(edge_id, sources, targets, edge_type="CALLS"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path="test.py", chunk_id=f"chunk_{edge_id}",
    )


def test_compute_node_idf():
    """IDF is higher for rare nodes than common ones."""
    builder = HypergraphBuilder()
    # 'hub' appears in 3 edges, 'rare' in 1
    builder.add_edge(_make_edge("e1", ["hub"], ["A", "rare"]))
    builder.add_edge(_make_edge("e2", ["hub"], ["B", "C"]))
    builder.add_edge(_make_edge("e3", ["hub"], ["D", "E"]))

    idf = builder.compute_node_idf()
    assert idf["hub"] < idf["rare"], (
        f"Hub idf ({idf['hub']:.2f}) should be lower than rare idf ({idf['rare']:.2f})"
    )
    # hub: log(1 + 3/3) = log(2) ≈ 0.69
    # rare: log(1 + 3/1) = log(4) ≈ 1.39
    assert abs(idf["hub"] - math.log(2)) < 0.01
    assert abs(idf["rare"] - math.log(4)) < 0.01


def test_get_hub_nodes_scales_with_graph_size():
    """Hub detection threshold adapts to graph size, not absolute degree."""
    builder = HypergraphBuilder()
    # Create 100 edges. 'hub' appears in all 100.
    # 'normal' appears in 2.
    for i in range(100):
        sources = ["hub", f"unique_{i}"]
        targets = [f"target_{i}"]
        if i < 2:
            sources.append("normal")
        builder.add_edge(_make_edge(f"e{i}", sources, targets))

    hubs = builder.get_hub_nodes(max_degree_pct=0.03)
    # 3% of 100 = 3. 'hub' has degree 100 → definitely a hub.
    assert "hub" in hubs
    # 'normal' has degree 2 → not a hub.
    assert "normal" not in hubs


def test_adjacent_edges_excludes_hub_nodes():
    """Hub nodes should not create adjacency connections."""
    builder = HypergraphBuilder()
    # e1 and e2 share 'hub' (which we'll exclude) and nothing else.
    # e1 and e3 share 'Session' (not a hub).
    builder.add_edge(_make_edge("e1", ["Session"], ["hub", "A"]))
    builder.add_edge(_make_edge("e2", ["hub"], ["B", "C"]))
    builder.add_edge(_make_edge("e3", ["Session"], ["D", "E"]))

    # Without filtering: e1 is adjacent to both e2 (via hub) and e3 (via Session)
    adj_unfiltered = builder.get_adjacent_edges("e1", s=1)
    adj_ids = {eid for eid, _ in adj_unfiltered}
    assert "e2" in adj_ids
    assert "e3" in adj_ids

    # With hub filtering: e1 is only adjacent to e3 (via Session)
    adj_filtered = builder.get_adjacent_edges("e1", s=1, exclude_nodes={"hub"})
    adj_ids_filtered = {eid for eid, _ in adj_filtered}
    assert "e2" not in adj_ids_filtered, "Hub-connected edge should be excluded"
    assert "e3" in adj_ids_filtered, "Non-hub connection should remain"


def test_idf_weighted_intersection_prefers_specific_nodes():
    """Intersection through specific nodes should score higher than through hubs."""
    builder = HypergraphBuilder()
    # 'int' appears in 20 edges (hub), 'Session' in 2 edges (specific)
    for i in range(20):
        builder.add_edge(_make_edge(f"e_int_{i}", ["int"], [f"x_{i}", f"y_{i}"]))
    builder.add_edge(_make_edge("e_sess_1", ["Session"], ["A", "B"]))
    builder.add_edge(_make_edge("e_sess_2", ["Session"], ["C", "D"]))

    idf = builder.compute_node_idf()
    # 22 total edges
    # int: log(1 + 22/20) = log(2.1) ≈ 0.74
    # Session: log(1 + 22/2) = log(12) ≈ 2.48
    assert idf["Session"] > 2 * idf["int"], (
        f"Session idf ({idf['Session']:.2f}) should be much higher than "
        f"int idf ({idf['int']:.2f})"
    )


def test_hub_node_floor():
    """At scale, the fixed floor catches builtins the percentage misses."""
    builder = HypergraphBuilder()
    # Create a large graph: 2000 edges, one node in 100 of them
    for i in range(2000):
        builder.add_edge(_make_edge(f"edge_{i}", [f"func_{i}"], [f"target_{i}"]))
    # Add a "builtin" node to 100 edges
    for i in range(100):
        builder.add_edge(_make_edge(
            f"builtin_edge_{i}", ["isinstance"], [f"btarget_{i}", f"bother_{i}"],
        ))
    hubs = builder.get_hub_nodes()
    # pct threshold = 3% of 2100 = 63, floor = 50, effective = min(63, 50) = 50
    # isinstance has 100 edges > 50 → should be a hub
    assert "isinstance" in hubs


def test_hub_node_small_graph():
    """For small graphs, percentage threshold is still used."""
    builder = HypergraphBuilder()
    for i in range(30):
        builder.add_edge(_make_edge(f"edge_{i}", [f"func_{i}"], [f"target_{i}"]))
    # Node in 4 edges — above 3% of 30 ~= 0.9, so threshold = max(2, 0) = 2
    for i in range(4):
        builder.add_edge(_make_edge(
            f"hub_edge_{i}", ["common"], [f"x_{i}", f"xx_{i}"],
        ))
    hubs = builder.get_hub_nodes()
    # pct threshold = max(2, int(34*0.03)) = max(2, 1) = 2, floor = 50
    # effective = min(2, 50) = 2. common has 4 edges > 2 → hub
    assert "common" in hubs
