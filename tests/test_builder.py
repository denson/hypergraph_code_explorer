"""Tests for HypergraphBuilder — inverted index, adjacency, removal."""

from __future__ import annotations

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord


def _make_record(edge_id: str, sources: list[str], targets: list[str],
                 edge_type: str = "CALLS", source_path: str = "test.py") -> HyperedgeRecord:
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path=source_path, chunk_id=f"chunk_{edge_id}",
    )


def test_add_edge_maintains_inverted_index():
    builder = HypergraphBuilder()
    rec = _make_record("e1", ["Session"], ["HTTPAdapter", "send"])
    builder.add_edge(rec)

    assert "Session" in builder._node_to_edges
    assert "e1" in builder._node_to_edges["Session"]
    assert "e1" in builder._node_to_edges["HTTPAdapter"]
    assert "e1" in builder._node_to_edges["send"]


def test_get_edges_for_node():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["Session"], ["HTTPAdapter", "send"]))
    builder.add_edge(_make_record("e2", ["HTTPAdapter"], ["poolmanager", "send_request"]))

    edges = builder.get_edges_for_node("HTTPAdapter")
    assert len(edges) == 2
    assert {e.edge_id for e in edges} == {"e1", "e2"}


def test_get_intersection():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["Session"], ["HTTPAdapter", "poolmanager", "send"]))
    builder.add_edge(_make_record("e2", ["HTTPAdapter"], ["poolmanager", "send_request"]))

    intersection = builder.get_intersection("e1", "e2")
    assert intersection == {"HTTPAdapter", "poolmanager"}


def test_get_adjacent_edges_s1():
    """Test from the spec: 5 AST edges, at s=1 should have 7 of 10 pairs connected."""
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["Session"], ["HTTPAdapter", "poolmanager", "send"]))
    builder.add_edge(_make_record("e2", ["HTTPAdapter"], ["poolmanager", "send_request"]))
    builder.add_edge(_make_record("e3", ["Session"], ["PreparedRequest", "send"]))
    builder.add_edge(_make_record("e4", ["poolmanager"], ["ConnectionPool", "urlopen"]))
    builder.add_edge(_make_record("e5", ["send"], ["HTTPAdapter", "timeout"]))

    # Count connected pairs at s=1
    edge_ids = ["e1", "e2", "e3", "e4", "e5"]
    connected_pairs = 0
    for i in range(len(edge_ids)):
        for j in range(i + 1, len(edge_ids)):
            intersection = builder.get_intersection(edge_ids[i], edge_ids[j])
            if len(intersection) >= 1:
                connected_pairs += 1

    assert connected_pairs == 7, f"Expected 7 connected pairs at s=1, got {connected_pairs}"


def test_get_adjacent_edges_s2():
    """At s=2 should have 3 of 10 pairs connected."""
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["Session"], ["HTTPAdapter", "poolmanager", "send"]))
    builder.add_edge(_make_record("e2", ["HTTPAdapter"], ["poolmanager", "send_request"]))
    builder.add_edge(_make_record("e3", ["Session"], ["PreparedRequest", "send"]))
    builder.add_edge(_make_record("e4", ["poolmanager"], ["ConnectionPool", "urlopen"]))
    builder.add_edge(_make_record("e5", ["send"], ["HTTPAdapter", "timeout"]))

    edge_ids = ["e1", "e2", "e3", "e4", "e5"]
    connected_pairs = 0
    for i in range(len(edge_ids)):
        for j in range(i + 1, len(edge_ids)):
            intersection = builder.get_intersection(edge_ids[i], edge_ids[j])
            if len(intersection) >= 2:
                connected_pairs += 1

    assert connected_pairs == 3, f"Expected 3 connected pairs at s=2, got {connected_pairs}"


def test_get_adjacent_edges_s3():
    """At s=3 should have 0 of 10 pairs connected."""
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["Session"], ["HTTPAdapter", "poolmanager", "send"]))
    builder.add_edge(_make_record("e2", ["HTTPAdapter"], ["poolmanager", "send_request"]))
    builder.add_edge(_make_record("e3", ["Session"], ["PreparedRequest", "send"]))
    builder.add_edge(_make_record("e4", ["poolmanager"], ["ConnectionPool", "urlopen"]))
    builder.add_edge(_make_record("e5", ["send"], ["HTTPAdapter", "timeout"]))

    edge_ids = ["e1", "e2", "e3", "e4", "e5"]
    connected_pairs = 0
    for i in range(len(edge_ids)):
        for j in range(i + 1, len(edge_ids)):
            intersection = builder.get_intersection(edge_ids[i], edge_ids[j])
            if len(intersection) >= 3:
                connected_pairs += 1

    assert connected_pairs == 0, f"Expected 0 connected pairs at s=3, got {connected_pairs}"


def test_remove_edges_by_file():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B"], source_path="a.py"))
    builder.add_edge(_make_record("e2", ["C"], ["D"], source_path="b.py"))
    builder.add_edge(_make_record("e3", ["A"], ["E"], source_path="a.py"))

    removed = builder.remove_edges_by_file("a.py")
    assert removed == 2
    assert "e1" not in builder._edge_store
    assert "e3" not in builder._edge_store
    assert "e2" in builder._edge_store
    # Inverted index should be updated
    assert "A" not in builder._node_to_edges or len(builder._node_to_edges.get("A", set())) == 0


def test_serialize_deserialize():
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["A"], ["B", "C"]))
    builder.add_edge(_make_record("e2", ["B"], ["D"]))

    data = builder.serialize()
    builder2 = HypergraphBuilder.deserialize(data)

    assert builder2._incidence.keys() == builder._incidence.keys()
    assert builder2.get_edge("e1").sources == ["A"]
    assert "B" in builder2._node_to_edges
    assert "e1" in builder2._node_to_edges["B"]


def test_duplicate_edge_rejected():
    builder = HypergraphBuilder()
    rec = _make_record("e1", ["A"], ["B"])
    assert builder.add_edge(rec) is True
    assert builder.add_edge(rec) is False
    assert len(builder._incidence) == 1


def test_single_node_edge_rejected():
    builder = HypergraphBuilder()
    rec = _make_record("e1", ["A"], [])
    rec.all_nodes = {"A"}
    assert builder.add_edge(rec) is False
