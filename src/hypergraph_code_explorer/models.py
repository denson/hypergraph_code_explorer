"""
Core Data Models
================
All dataclasses and enums for the hypergraph code explorer.
Implements directed hyperedges with source/target metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Edge types and weights
# ---------------------------------------------------------------------------

class EdgeType(str, Enum):
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    DEFINES = "DEFINES"
    INHERITS = "INHERITS"
    SIGNATURE = "SIGNATURE"
    RAISES = "RAISES"
    DECORATES = "DECORATES"
    TEXT = "TEXT"
    SUMMARY = "SUMMARY"


DEFAULT_TYPE_WEIGHTS: dict[str, float] = {
    EdgeType.CALLS: 1.0,
    EdgeType.IMPORTS: 1.0,
    EdgeType.DEFINES: 1.0,
    EdgeType.INHERITS: 1.0,
    EdgeType.SIGNATURE: 1.0,
    EdgeType.RAISES: 1.0,
    EdgeType.DECORATES: 1.0,
    EdgeType.TEXT: 0.7,
    EdgeType.SUMMARY: 0.3,
}

DEFAULT_INTERSECTION_THRESHOLDS: dict[str, int] = {
    EdgeType.CALLS: 1,
    EdgeType.IMPORTS: 1,
    EdgeType.DEFINES: 1,
    EdgeType.INHERITS: 1,
    EdgeType.SIGNATURE: 1,
    EdgeType.RAISES: 1,
    EdgeType.DECORATES: 1,
    EdgeType.TEXT: 2,
    EdgeType.SUMMARY: 1,
}


# ---------------------------------------------------------------------------
# Hyperedge record — directed, with source/target metadata
# ---------------------------------------------------------------------------

@dataclass
class HyperedgeRecord:
    """A single directed hyperedge in the graph."""
    edge_id: str
    relation: str
    edge_type: str
    sources: list[str]
    targets: list[str]
    all_nodes: set[str] = field(default_factory=set)
    source_path: str = ""
    chunk_id: str = ""
    chunk_text: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.all_nodes:
            self.all_nodes = set(self.sources) | set(self.targets)

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "relation": self.relation,
            "edge_type": self.edge_type,
            "sources": self.sources,
            "targets": self.targets,
            "all_nodes": sorted(self.all_nodes),
            "source_path": self.source_path,
            "chunk_id": self.chunk_id,
            "chunk_text": self.chunk_text,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HyperedgeRecord:
        return cls(
            edge_id=d["edge_id"],
            relation=d["relation"],
            edge_type=d["edge_type"],
            sources=d["sources"],
            targets=d["targets"],
            all_nodes=set(d.get("all_nodes", [])),
            source_path=d.get("source_path", ""),
            chunk_id=d.get("chunk_id", ""),
            chunk_text=d.get("chunk_text", ""),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Traversal and path data structures
# ---------------------------------------------------------------------------

@dataclass
class TraversalHop:
    """A single hop in a traversal path between two edges."""
    from_edge: str
    to_edge: str
    intersection_nodes: list[str]
    from_members: list[str] = field(default_factory=list)
    to_members: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "from_edge": self.from_edge,
            "to_edge": self.to_edge,
            "intersection_nodes": self.intersection_nodes,
            "from_members": self.from_members,
            "to_members": self.to_members,
        }


@dataclass
class PathReport:
    """A path through hyperedge space from source to target."""
    edges: list[str]
    hops: list[TraversalHop]
    start_comembers: list[str] = field(default_factory=list)
    end_comembers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "edges": self.edges,
            "hops": [h.to_dict() for h in self.hops],
            "start_comembers": self.start_comembers,
            "end_comembers": self.end_comembers,
        }


# ---------------------------------------------------------------------------
# Retrieval result types
# ---------------------------------------------------------------------------

@dataclass
class ScoredEdge:
    """An edge with retrieval scoring metadata."""
    edge: HyperedgeRecord
    weighted_precision: float = 0.0
    coverage: float = 0.0
    score: float = 0.0
    retrieval_source: str = "seed"  # "seed" or "intersection"
    matched_nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge.edge_id,
            "relation": self.edge.relation,
            "edge_type": self.edge.edge_type,
            "sources": self.edge.sources,
            "targets": self.edge.targets,
            "score": round(self.score, 4),
            "weighted_precision": round(self.weighted_precision, 4),
            "coverage": round(self.coverage, 4),
            "retrieval_source": self.retrieval_source,
            "matched_nodes": self.matched_nodes,
            "source_path": self.edge.source_path,
        }


@dataclass
class RetrievalResult:
    """Full result from the retrieval pipeline."""
    query: str
    matched_nodes: list[tuple[str, float]]
    scored_edges: list[ScoredEdge]
    traversal_paths: list[PathReport] = field(default_factory=list)
    coverage_score: float = 0.0
    intersection_density: float = 0.0
    retrieval_source_breakdown: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "matched_nodes": [
                {"node": n, "score": round(s, 4)} for n, s in self.matched_nodes
            ],
            "retrieved_edges": [se.to_dict() for se in self.scored_edges],
            "traversal_paths": [p.to_dict() for p in self.traversal_paths],
            "coverage_score": round(self.coverage_score, 4),
            "intersection_density": round(self.intersection_density, 4),
            "retrieval_source_breakdown": self.retrieval_source_breakdown,
        }


@dataclass
class CoverageResult:
    """Result from the coverage evaluation tool."""
    covered_nodes: list[str]
    uncovered_nodes: list[str]
    frontier_nodes: list[dict]
    coverage_score: float
    intersection_density: float
    retrieval_source_breakdown: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "covered_nodes": self.covered_nodes,
            "uncovered_nodes": self.uncovered_nodes,
            "frontier_nodes": self.frontier_nodes,
            "coverage_score": round(self.coverage_score, 4),
            "intersection_density": round(self.intersection_density, 4),
            "retrieval_source_breakdown": self.retrieval_source_breakdown,
        }
