"""
API Layer
=========
HypergraphSession — serializable API layer between pipeline and MCP server.
Exposes the new v3 retrieval interface (lookup, search, query, overview)
alongside legacy methods for backward compatibility.

Memory tour operations are delegated to MemoryTourStore, loaded lazily from
the same cache directory that holds builder.pkl.
"""

from __future__ import annotations

import json
from pathlib import Path

from .graph.builder import HypergraphBuilder
from .memory_tours import (
    MemoryTour,
    MemoryTourStep,
    MemoryTourStore,
    scaffold_from_plan,
    scaffold_prompt,
)
from .retrieval.plan import RetrievalPlan, format_text, format_json


class HypergraphSession:
    """Wraps a HypergraphBuilder for use by the MCP server and API consumers."""

    def __init__(
        self,
        builder: HypergraphBuilder | None = None,
        cache_dir: str | Path | None = None,
    ):
        self._builder = builder or HypergraphBuilder()
        self._cache_dir: Path | None = Path(cache_dir) if cache_dir else None
        self._tour_store: MemoryTourStore | None = None

    @classmethod
    def load(cls, path: str | Path) -> HypergraphSession:
        """Load a session from a saved builder state."""
        path = Path(path)
        if path.is_dir():
            cache_dir = path
            builder_path = path / "builder.pkl"
        else:
            cache_dir = path.parent
            builder_path = path
        builder = HypergraphBuilder.load(builder_path)
        return cls(builder, cache_dir=cache_dir)

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

    # ---- Memory tour operations -------------------------------------------

    def _get_tour_store(self) -> MemoryTourStore:
        """Lazily initialise and return the MemoryTourStore."""
        if self._tour_store is None:
            if self._cache_dir is None:
                raise RuntimeError(
                    "No cache directory available for memory tours. "
                    "Load the session from a cache directory or set cache_dir."
                )
            self._tour_store = MemoryTourStore(self._cache_dir)
        return self._tour_store

    # ---- Active tour management ------------------------------------------

    def start_tour(self, name: str, *, tags: list[str] | None = None) -> MemoryTour:
        """Create a new empty tour and set it as the active investigation tour."""
        store = self._get_tour_store()
        tour = MemoryTour(
            id="",  # auto-generated
            name=name,
            summary=f"Investigation: {name}",
            tags=tags or [],
        )
        store.add(tour)
        store.set_active_tour(tour.id)
        return tour

    def stop_tour(self) -> MemoryTour | None:
        """Stop the active tour. Returns the stopped tour, or None."""
        store = self._get_tour_store()
        tour_id = store.get_active_tour_id()
        if not tour_id:
            return None
        tour = store.get(tour_id)
        store.clear_active_tour()
        return tour

    def resume_tour(self, tour_id: str) -> MemoryTour | None:
        """Resume an existing tour as the active tour."""
        store = self._get_tour_store()
        tour = store.get(tour_id)
        if tour is None:
            return None
        store.set_active_tour(tour_id)
        return tour

    def get_active_tour(self) -> MemoryTour | None:
        """Return the active tour, or None."""
        store = self._get_tour_store()
        tour_id = store.get_active_tour_id()
        if not tour_id:
            return None
        return store.get(tour_id)

    def append_to_active_tour(
        self, steps: list[MemoryTourStep],
    ) -> tuple[int, int] | None:
        """Append steps to the active tour with deduplication.

        Returns (added, skipped) or None if no active tour.
        """
        store = self._get_tour_store()
        tour_id = store.get_active_tour_id()
        if not tour_id:
            return None
        return store.append_steps(tour_id, steps)

    def memory_tour_create(
        self,
        plan: RetrievalPlan,
        *,
        name: str = "",
        tags: list[str] | None = None,
    ) -> MemoryTour:
        """Scaffold a memory tour from a RetrievalPlan and persist it."""
        tour = scaffold_from_plan(plan, name=name, tags=tags)
        return self._get_tour_store().add(tour)

    def memory_tour_create_from_dict(self, data: dict) -> MemoryTour:
        """Create a memory tour from raw dict data (e.g. LLM-authored JSON)."""
        tour = MemoryTour.from_dict(data)
        return self._get_tour_store().add(tour)

    def memory_tour_list(
        self,
        *,
        tag: str | None = None,
        promoted_only: bool = False,
    ) -> list[dict]:
        """List all memory tours as dicts."""
        tours = self._get_tour_store().list_tours(
            tag=tag, promoted_only=promoted_only,
        )
        return [t.to_dict() for t in tours]

    def memory_tour_get(self, tour_id: str) -> dict | None:
        """Get a single memory tour by ID."""
        tour = self._get_tour_store().get(tour_id)
        if tour:
            self._get_tour_store().touch(tour_id)
            return tour.to_dict()
        return None

    def memory_tour_promote(self, tour_id: str) -> dict | None:
        """Mark a memory tour as promoted (persistent)."""
        tour = self._get_tour_store().promote(tour_id)
        return tour.to_dict() if tour else None

    def memory_tour_remove(self, tour_id: str) -> bool:
        """Remove a memory tour."""
        return self._get_tour_store().remove(tour_id)

    def memory_tour_scaffold_prompt(
        self,
        plan: RetrievalPlan,
    ) -> str:
        """Generate a structured prompt for LLM-authored memory tour creation."""
        existing = [
            t.name for t in self._get_tour_store().list_tours()
        ]
        return scaffold_prompt(plan, existing_tour_names=existing)

    def annotate_tour(
        self,
        tour_id: str,
        *,
        finding: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> dict | None:
        """Update a tour's finding, status, or tags."""
        store = self._get_tour_store()
        tour = store.get(tour_id)
        if tour is None:
            return None
        if finding is not None:
            tour.finding = finding
        if status is not None:
            store.set_status(tour_id, status)
        if tags:
            tour.tags.extend(tags)
            store.save()
        return tour.to_dict()

    def export_tours(
        self,
        *,
        tour_ids: list[str] | None = None,
        status: str | None = None,
    ) -> dict:
        """Export tours as a standalone dict (ready for JSON serialization)."""
        from datetime import datetime, timezone

        store = self._get_tour_store()
        if tour_ids:
            tours = [store.get(tid) for tid in tour_ids]
            tours = [t for t in tours if t is not None]
        elif status:
            tours = store.list_tours(status=status)
        else:
            tours = store.list_tours()

        return {
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_cache_dir": str(self._cache_dir) if self._cache_dir else "",
            "tours": [t.to_dict() for t in tours],
        }

    def import_tours(
        self,
        data: dict,
        *,
        overwrite: bool = False,
    ) -> dict:
        """Import tours from an export dict. Returns counts."""
        store = self._get_tour_store()
        imported = 0
        skipped = 0
        for td in data.get("tours", []):
            tour = MemoryTour.from_dict(td)
            existing = store.get(tour.id)
            if existing and not overwrite:
                skipped += 1
                continue
            store._tours[tour.id] = tour
            imported += 1
        store.save()
        return {"imported": imported, "skipped": skipped}

    # ---- Probe (single structural probe into the graph) --------------------

    def probe(
        self,
        question: str,
        *,
        depth: int = 2,
        max_tour_steps: int = 200,
        tags: list[str] | None = None,
        strategies: list[str] | None = None,
    ) -> MemoryTour:
        """Run a single structural probe from a plain-English question.

        Unlike blast_radius() which is anchored to a single symbol's BFS
        neighborhood, probe() decomposes the question into multiple queries,
        merges the results, and builds a tour covering all relevant code.

        A probe is one building block in a multi-query investigation — not
        a complete analysis on its own.
        """
        from .probe import probe as run_probe
        return run_probe(
            question, self,
            depth=depth,
            max_tour_steps=max_tour_steps,
            tags=tags,
            strategies=strategies,
        )

    # Deprecated alias
    analyze = probe

    # ---- Blast radius analysis -------------------------------------------

    def blast_radius(
        self,
        symbol: str,
        *,
        depth: int = 2,
        task_description: str = "",
        tags: list[str] | None = None,
    ) -> MemoryTour:
        """Generate a task-oriented tour for blast radius analysis.

        Runs multi-perspective lookups (all edges, RAISES, INHERITS), merges
        the results, scaffolds a tour, and annotates each step with a
        task-specific ``context_query``.
        """
        # Multi-perspective lookups
        plan_all = self.lookup(symbol, direction="both", depth=depth)
        plan_raises = self.lookup(
            symbol, edge_types=["RAISES"], direction="both", depth=depth,
        )
        plan_inherits = self.lookup(
            symbol, edge_types=["INHERITS"], direction="both", depth=depth,
        )

        # Merge (plan.merge deduplicates internally)
        plan_all.merge(plan_raises)
        plan_all.merge(plan_inherits)

        # Scaffold the tour
        tour_tags = ["blast-radius"] + (tags or [])
        tour = scaffold_from_plan(
            plan_all,
            name=f"Blast radius: {symbol}",
            tags=tour_tags,
        )

        # Annotate each step with a context_query based on edge type
        task_clause = task_description or f"changes to {symbol}"
        for step in tour.steps:
            et = step.edge_type.upper() if step.edge_type else ""
            if et == "RAISES":
                step.context_query = (
                    f"Does this location raise or catch {symbol}? "
                    f"How would it be affected by: {task_clause}"
                )
            elif et == "CALLS":
                step.context_query = (
                    f"This code calls {symbol} or is called by it. "
                    f"Would the behavior change if {task_clause}?"
                )
            elif et == "INHERITS":
                step.context_query = (
                    f"This inherits from or is inherited by {symbol}. "
                    f"Would it be affected by: {task_clause}"
                )
            else:
                step.context_query = (
                    f"How does this symbol interact with {symbol}? "
                    f"Would it be affected by: {task_clause}"
                )

        # Persist and return
        store = self._get_tour_store()
        store.add(tour)
        return tour

    # ---- Visualization ---------------------------------------------------

    def visualize(
        self,
        *,
        tags: list[str] | None = None,
        tour_ids: list[str] | None = None,
        full_graph: bool = False,
        max_neighborhood_hops: int = 0,
        max_svg: int = 500,
        output: str = "visualization",
        title: str = "",
    ) -> dict:
        """Generate D3 HTML visualization, optionally with tour overlays.

        If ``full_graph`` is True, visualizes the entire graph with no tour
        filtering. Otherwise, selects tours by tag or ID; if no tours match,
        falls back to full-graph mode.

        Args:
            max_neighborhood_hops: Maximum hops to emit (0 = unlimited).
                Nodes beyond are hard-pruned from the data. The D3 template
                uses hop_distance + zoom level for fog-of-war visibility.

        Returns dict with keys: html, md (or None), tours, nodes, edges,
        fog_tour_nodes, fog_near, fog_far.
        """
        from .visualization import select_tours, generate_visualization

        tours = None
        if not full_graph:
            store = self._get_tour_store()
            selected = select_tours(store, tags=tags, tour_ids=tour_ids)
            if selected:
                tours = selected

        viz_title = title or "Codebase Architecture"
        target_codebase = str(self._cache_dir) if self._cache_dir else ""

        return generate_visualization(
            self._builder, output,
            tours=tours,
            max_neighborhood_hops=max_neighborhood_hops,
            max_svg=max_svg,
            title=viz_title,
            target_codebase=target_codebase,
        )

    # ---- Persistence ---------------------------------------------------

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._builder.save(path / "builder.pkl")