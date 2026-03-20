"""Tests for core data models."""

from __future__ import annotations

from hypergraph_code_explorer.models import (
    CoverageResult,
    EdgeType,
    HyperedgeRecord,
    PathReport,
    RetrievalResult,
    ScoredEdge,
    TraversalHop,
)


def test_edge_type_values():
    assert EdgeType.CALLS == "CALLS"
    assert EdgeType.SUMMARY == "SUMMARY"


def test_hyperedge_record_auto_all_nodes():
    rec = HyperedgeRecord(
        edge_id="e1",
        relation="calls",
        edge_type="CALLS",
        sources=["Session"],
        targets=["HTTPAdapter", "send"],
    )
    assert rec.all_nodes == {"Session", "HTTPAdapter", "send"}


def test_hyperedge_record_roundtrip():
    rec = HyperedgeRecord(
        edge_id="e1",
        relation="calls",
        edge_type="CALLS",
        sources=["Session"],
        targets=["HTTPAdapter"],
        source_path="test.py",
        chunk_id="c1",
        chunk_text="def send(): ...",
    )
    d = rec.to_dict()
    rec2 = HyperedgeRecord.from_dict(d)
    assert rec2.edge_id == rec.edge_id
    assert rec2.sources == rec.sources
    assert rec2.targets == rec.targets
    assert rec2.all_nodes == rec.all_nodes


def test_traversal_hop_to_dict():
    hop = TraversalHop(
        from_edge="e1",
        to_edge="e2",
        intersection_nodes=["HTTPAdapter"],
    )
    d = hop.to_dict()
    assert d["from_edge"] == "e1"
    assert d["intersection_nodes"] == ["HTTPAdapter"]


def test_path_report_to_dict():
    path = PathReport(
        edges=["e1", "e2"],
        hops=[TraversalHop(from_edge="e1", to_edge="e2", intersection_nodes=["X"])],
    )
    d = path.to_dict()
    assert len(d["hops"]) == 1


def test_scored_edge_to_dict():
    rec = HyperedgeRecord(
        edge_id="e1", relation="calls", edge_type="CALLS",
        sources=["A"], targets=["B"],
    )
    se = ScoredEdge(edge=rec, score=0.85, retrieval_source="seed")
    d = se.to_dict()
    assert d["score"] == 0.85
    assert d["retrieval_source"] == "seed"


def test_retrieval_result_to_dict():
    rec = HyperedgeRecord(
        edge_id="e1", relation="calls", edge_type="CALLS",
        sources=["A"], targets=["B"],
    )
    result = RetrievalResult(
        query="test",
        matched_nodes=[("A", 0.9)],
        scored_edges=[ScoredEdge(edge=rec, score=0.8)],
        coverage_score=0.7,
    )
    d = result.to_dict()
    assert d["query"] == "test"
    assert len(d["retrieved_edges"]) == 1


def test_coverage_result_to_dict():
    cr = CoverageResult(
        covered_nodes=["A", "B"],
        uncovered_nodes=["C"],
        frontier_nodes=[{"node": "D", "incident_edge_count": 3, "suggested_query": "D impl"}],
        coverage_score=0.67,
        intersection_density=0.5,
    )
    d = cr.to_dict()
    assert d["coverage_score"] == 0.67
