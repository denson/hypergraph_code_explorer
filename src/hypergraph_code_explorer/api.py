"""
API Layer
=========
HypergraphSession — serializable API layer between pipeline and MCP server.
Exposes the new v3 retrieval interface (lookup, search, query, overview)
alongside legacy methods for backward compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path

from .graph.builder import HypergraphBuilder
from .retrieval.plan import RetrievalPlan, format_text, format_json


class HypergraphSession:
    """Wraps a HypergraphBuilder for use by the MCP server and API consumers."""

    def __init__(self, builder: HypergraphBuilder | None = None):
        self._builder = builder or HypergraphBuilder()

    @classmethod
    def load(cls, path: str | Path) -> HypergraphSession:
        """Load a session from a saved builder state."""
        path = Path(path)
        builder_path = path / "builder.pkl" if path.is_dir() else path
        builder = HypergraphBuilder.load(builder_path)
        return cls(builder)

    @property
    def builder(self) -> HypergraphBuilder:
        return self._builder

    # ---- v3 retrieval methods (RetrievalPlan-based) -------------------------

    def lookup(
        self,
        symbol: str,
        *,
        edge_types: list[str] | None = None,
        depth: int = 1,
        direction: str = "both",
    ) -> RetrievalPlan:
        """Look up a symbol via Tier 1 exact match + optional Tier 2 traversal."""
        from .retrieval.lookup import lookup as tier1_lookup
        from .retrieval.traverse import traverse

        plan = tier1_lookup(symbol, self._builder, edge_types=edge_types)

        if depth > 0 and not plan.is_empty():
            seed_nodes = list({s.name for s in plan.related_symbols})
            t2 = traverse(
                seed_nodes[:5], self._builder,
                edge_types=edge_types, depth=depth, direction=direction,
            )
            plan.merge(t2)

        return plan

    def search(
        self,
        term: str,
        *,
        max_results: int = 20,
    ) -> RetrievalPlan:
        """Text search across all symbols (Tier 3)."""
        from .retrieval.textsearch import text_search
        return text_search(term, self._builder, max_results=max_results)

    def query(
        self,
        query: str,
        *,
        depth: int = 2,
        max_results: int = 20,
        edge_types: list[str] | None = None,
    ) -> RetrievalPlan:
        """Full dispatch query through all tiers."""
        from .retrieval.dispatch import dispatch
        return dispatch(
            query, self._builder,
            depth=depth, max_results=max_results, edge_types=edge_types,
        )

    def overview(
        self,
        *,
        top: int = 10,
    ) -> dict:
        """Generate a codebase overview."""
        from .retrieval.plan import Overview

        # Gather modules
        modules: list[dict] = []
        all_paths: set[str] = set()
        for rec in self._builder._edge_store.values():
            if rec.source_path:
                all_paths.add(rec.source_path)
        for path in sorted(all_paths):
            modules.append({"path": path})

        # Key symbols by degree
        key_symbols: list[dict] = []
        for node, edge_ids in sorted(
            self._builder._node_to_edges.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )[:top]:
            key_symbols.append({"name": node, "degree": len(edge_ids)})

        overview = Overview(
            modules=modules,
            key_symbols=key_symbols,
            reading_order=[],
        )
        return overview.to_dict()

    def stats(self) -> dict:
        """Graph statistics."""
        stats = self._builder.stats()
        stats["hub_nodes"] = len(self._builder.get_hub_nodes())
        return stats

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._builder.save(path / "builder.pkl")
