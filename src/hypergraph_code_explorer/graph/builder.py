"""
Hypergraph Builder
==================
Core data structure: incidence dict + inverted index + directed edge store.
The inverted index (_node_to_edges) is maintained on every add_edge call,
making intersection traversal fast (O(1) node → edges lookup).
"""

from __future__ import annotations

import json
import pickle
import re
from collections import defaultdict
from pathlib import Path

from ..models import HyperedgeRecord


# ---------------------------------------------------------------------------
# Node normalisation
# ---------------------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def normalise_node(name: str) -> str:
    """Normalise an entity name. Preserves case (important for code)."""
    name = _CONTROL_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class HypergraphBuilder:
    """
    Builds and maintains a hypergraph with an explicit inverted index.

    Core data structures:
        _incidence:     edge_id → set of node names
        _node_to_edges: node → set of edge_ids (INVERTED INDEX)
        _edge_store:    edge_id → HyperedgeRecord
        _chunk_registry: chunk_id → chunk_text
    """

    def __init__(self):
        self._incidence: dict[str, set[str]] = {}
        self._node_to_edges: dict[str, set[str]] = defaultdict(set)
        self._edge_store: dict[str, HyperedgeRecord] = {}
        self._chunk_registry: dict[str, str] = {}

    # ---- adding edges ------------------------------------------------------

    def add_edge(self, record: HyperedgeRecord) -> bool:
        """Add a single HyperedgeRecord. Returns True if added, False if duplicate."""
        if not record.all_nodes or len(record.all_nodes) < 2:
            return False

        # Normalise nodes
        norm_nodes = {normalise_node(n) for n in record.all_nodes if normalise_node(n)}
        if len(norm_nodes) < 2:
            return False

        if record.edge_id in self._incidence:
            return False

        # Update all data structures atomically
        record.all_nodes = norm_nodes
        record.sources = [normalise_node(s) for s in record.sources if normalise_node(s)]
        record.targets = [normalise_node(t) for t in record.targets if normalise_node(t)]

        self._incidence[record.edge_id] = norm_nodes
        self._edge_store[record.edge_id] = record

        # Maintain inverted index
        for node in norm_nodes:
            self._node_to_edges[node].add(record.edge_id)

        # Register chunk text
        if record.chunk_id and record.chunk_text:
            self._chunk_registry[record.chunk_id] = record.chunk_text

        return True

    def add_edges(self, records: list[HyperedgeRecord]) -> int:
        """Add multiple edges. Returns count of edges actually added."""
        return sum(1 for r in records if self.add_edge(r))

    # ---- querying ----------------------------------------------------------

    def get_edge(self, edge_id: str) -> HyperedgeRecord | None:
        return self._edge_store.get(edge_id)

    def get_edges_for_node(self, node: str) -> list[HyperedgeRecord]:
        """Get all edges incident on a node (O(1) lookup via inverted index)."""
        node = normalise_node(node)
        edge_ids = self._node_to_edges.get(node, set())
        return [self._edge_store[eid] for eid in edge_ids if eid in self._edge_store]

    def get_edge_ids_for_node(self, node: str) -> set[str]:
        """Get edge IDs incident on a node."""
        node = normalise_node(node)
        return set(self._node_to_edges.get(node, set()))

    def get_intersection(self, edge_id_1: str, edge_id_2: str) -> set[str]:
        """Get the set of nodes shared between two edges."""
        nodes_1 = self._incidence.get(edge_id_1, set())
        nodes_2 = self._incidence.get(edge_id_2, set())
        return nodes_1 & nodes_2

    def get_adjacent_edges(
        self,
        edge_id: str,
        s: int = 1,
        exclude_nodes: set[str] | None = None,
    ) -> list[tuple[str, set[str]]]:
        """
        Find edges sharing ≥ s nodes with the given edge.
        Returns list of (adjacent_edge_id, intersection_nodes).

        Args:
            edge_id: The edge to find neighbours for.
            s: Minimum intersection size.
            exclude_nodes: Nodes to ignore (e.g., hub nodes). These are not
                counted toward the intersection size and not included in the
                returned intersection sets. This prevents high-degree nodes
                like 'int' or 'isinstance' from creating spurious adjacency.
        """
        nodes = self._incidence.get(edge_id, set())
        if not nodes:
            return []

        # Filter out hub nodes for candidate collection
        effective_nodes = nodes - exclude_nodes if exclude_nodes else nodes
        if not effective_nodes:
            return []

        # Collect candidate edges via inverted index (only through non-hub nodes)
        candidate_edges: set[str] = set()
        for node in effective_nodes:
            candidate_edges.update(self._node_to_edges.get(node, set()))
        candidate_edges.discard(edge_id)

        results = []
        for cand_id in candidate_edges:
            cand_nodes = self._incidence.get(cand_id, set())
            intersection = effective_nodes & cand_nodes
            if exclude_nodes:
                intersection -= exclude_nodes
            if len(intersection) >= s:
                results.append((cand_id, intersection))

        return results

    def get_all_nodes(self) -> set[str]:
        """Return all nodes in the graph."""
        all_nodes: set[str] = set()
        for nodes in self._incidence.values():
            all_nodes.update(nodes)
        return all_nodes

    def get_node_degree(self, node: str) -> int:
        """Number of edges incident on a node."""
        return len(self._node_to_edges.get(normalise_node(node), set()))

    def compute_node_idf(self) -> dict[str, float]:
        """Compute IDF (Inverse Document Frequency) for every node.

        idf(n) = log(1 + total_edges / degree(n))

        High-degree "hub" nodes (int, isinstance, etc.) get low IDF.
        Specific nodes (Session.send, HTTPAdapter) get high IDF.
        Adapts automatically to any codebase size and language.
        """
        import math
        total_edges = len(self._incidence)
        if total_edges == 0:
            return {}
        idf: dict[str, float] = {}
        for node, edge_ids in self._node_to_edges.items():
            degree = len(edge_ids)
            idf[node] = math.log(1 + total_edges / degree)
        return idf

    def get_hub_nodes(self, max_degree_pct: float = 0.03, min_degree_floor: int = 50) -> set[str]:
        """Return nodes whose degree exceeds max_degree_pct * total_edges OR min_degree_floor.

        These "hub" nodes appear in so many edges that they create
        spurious adjacency connections. Uses a hybrid threshold:
          - Percentage-based: adapts to graph size (3% of edges)
          - Fixed floor: catches builtins/generics in large graphs
        The LOWER of the two thresholds is used (i.e., more aggressive filtering).

        Examples:
          - 500 edges -> pct threshold = 15, floor = 50, effective = 15
          - 20,000 edges -> pct threshold = 600, floor = 50, effective = 50
        """
        total_edges = len(self._incidence)
        pct_threshold = max(2, int(total_edges * max_degree_pct))
        threshold = min(pct_threshold, min_degree_floor)
        return {
            node for node, edge_ids in self._node_to_edges.items()
            if len(edge_ids) > threshold
        }

    # ---- removal -----------------------------------------------------------

    def remove_edges_by_file(self, source_path: str) -> int:
        """Remove all edges originating from a file. Returns count removed."""
        to_remove = [
            eid for eid, rec in self._edge_store.items()
            if rec.source_path == source_path
        ]

        for eid in to_remove:
            nodes = self._incidence.pop(eid, set())
            for node in nodes:
                self._node_to_edges[node].discard(eid)
                if not self._node_to_edges[node]:
                    del self._node_to_edges[node]
            del self._edge_store[eid]

        return len(to_remove)

    def remove_edge(self, edge_id: str) -> bool:
        """Remove a single edge by ID."""
        if edge_id not in self._edge_store:
            return False

        nodes = self._incidence.pop(edge_id, set())
        for node in nodes:
            self._node_to_edges[node].discard(edge_id)
            if not self._node_to_edges[node]:
                del self._node_to_edges[node]
        del self._edge_store[edge_id]
        return True

    # ---- statistics --------------------------------------------------------

    def stats(self) -> dict:
        all_nodes = self.get_all_nodes()
        edge_sizes = [len(nodes) for nodes in self._incidence.values()]
        edge_types: dict[str, int] = defaultdict(int)
        for rec in self._edge_store.values():
            edge_types[rec.edge_type] += 1

        return {
            "num_nodes": len(all_nodes),
            "num_edges": len(self._incidence),
            "avg_edge_size": sum(edge_sizes) / len(edge_sizes) if edge_sizes else 0,
            "max_edge_size": max(edge_sizes) if edge_sizes else 0,
            "min_edge_size": min(edge_sizes) if edge_sizes else 0,
            "edge_type_counts": dict(edge_types),
            "num_chunks": len(self._chunk_registry),
        }

    # ---- serialisation -----------------------------------------------------

    def serialize(self) -> dict:
        """Serialize the builder state to a JSON-compatible dict."""
        return {
            "incidence": {k: sorted(v) for k, v in self._incidence.items()},
            "edge_store": {
                eid: rec.to_dict() for eid, rec in self._edge_store.items()
            },
            "chunk_registry": self._chunk_registry,
        }

    @classmethod
    def deserialize(cls, data: dict) -> HypergraphBuilder:
        """Reconstruct a builder from serialized data."""
        builder = cls()
        for eid, nodes in data["incidence"].items():
            builder._incidence[eid] = set(nodes)
            for node in nodes:
                builder._node_to_edges[node].add(eid)
        for eid, rec_dict in data["edge_store"].items():
            builder._edge_store[eid] = HyperedgeRecord.from_dict(rec_dict)
        builder._chunk_registry = data.get("chunk_registry", {})
        return builder

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self.serialize(), f)

    @classmethod
    def load(cls, path: str | Path) -> HypergraphBuilder:
        with open(path, "rb") as f:
            data = pickle.load(f)
        return cls.deserialize(data)
