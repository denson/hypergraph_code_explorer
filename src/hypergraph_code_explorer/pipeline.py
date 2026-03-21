"""
Pipeline Orchestrator
=====================
Sequence: discover files → convert → chunk → extract edges → build graph →
simplify → generate summaries → generate codemap.

Embeddings are optional (Tier 4) and only computed when explicitly requested.

File-hash caching: store manifest {file_path: (sha256, [edge_ids])}.
On re-index: skip unchanged, re-extract modified, add new, remove deleted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .extraction.code_extractor import CodeHyperedgeExtractor
from .graph.builder import HypergraphBuilder
from .graph.simplify import simplify_graph
from .graph.summaries import generate_summaries
from .ingestion.chunker import ContentAwareChunker
from .ingestion.converter import DocumentConverter
from .models import HyperedgeRecord


class HypergraphPipeline:
    """Orchestrates the full indexing and query pipeline."""

    def __init__(
        self,
        verbose: bool = False,
        text_edges: bool = False,
        skip_summaries: bool = False,
        summary_model: str = "claude-haiku-4-5-20251001",
    ):
        self.verbose = verbose
        self.text_edges = text_edges
        self.skip_summaries = skip_summaries
        self.summary_model = summary_model

        self.converter = DocumentConverter(verbose=verbose)
        self.chunker = ContentAwareChunker()
        self.code_extractor = CodeHyperedgeExtractor()
        self.builder = HypergraphBuilder()

        self._manifest: dict[str, tuple[str, list[str]]] = {}
        self._cache_dir: Path | None = None

    # ---- indexing -----------------------------------------------------------

    def index_directory(
        self,
        directory: str | Path,
        extensions: set[str] | None = None,
        anthropic_client=None,
    ) -> dict:
        """
        Index a directory: convert → chunk → extract → build → simplify → summarize → codemap.

        Embeddings are NOT computed by default. Use `hce embed` or `hce index --embed`.

        Returns a stats dict.
        """
        directory = Path(directory).resolve()
        self._cache_dir = directory / ".hce_cache"
        self._cache_dir.mkdir(exist_ok=True)

        # Load existing state if available
        self._load_state()

        if self.verbose:
            print(f"Indexing: {directory}")

        # Discover and convert files
        docs = self.converter.convert_directory(directory, extensions=extensions)

        if self.verbose:
            print(f"  Found {len(docs)} files")

        # File-hash caching: determine what changed
        current_files: dict[str, str] = {}  # path → sha256
        for doc in docs:
            file_hash = _hash_content(doc.markdown)
            current_files[doc.source_path] = file_hash

        # Determine changes
        old_paths = set(self._manifest.keys())
        new_paths = set(current_files.keys())

        deleted = old_paths - new_paths
        added = new_paths - old_paths
        possibly_modified = old_paths & new_paths
        modified = {
            p for p in possibly_modified
            if current_files[p] != self._manifest[p][0]
        }
        unchanged = possibly_modified - modified

        if self.verbose:
            print(f"  Changed: {len(added)} added, {len(modified)} modified, "
                  f"{len(deleted)} deleted, {len(unchanged)} unchanged")

        # Remove deleted and modified
        for path in deleted | modified:
            self.builder.remove_edges_by_file(path)
            del self._manifest[path]

        # Process added and modified files
        docs_to_process = [d for d in docs if d.source_path in (added | modified)]

        total_edges = 0
        for doc in docs_to_process:
            chunks = self.chunker.chunk(doc)
            edges = self.code_extractor.extract_all(chunks)

            # Text extraction (opt-in)
            if self.text_edges and anthropic_client:
                from .extraction.text_extractor import TextHyperedgeExtractor
                text_extractor = TextHyperedgeExtractor(
                    anthropic_client, verbose=self.verbose,
                )
                edges.extend(text_extractor.extract_all(chunks))

            added_count = self.builder.add_edges(edges)
            total_edges += added_count

            # Update manifest
            edge_ids = [e.edge_id for e in edges]
            self._manifest[doc.source_path] = (current_files[doc.source_path], edge_ids)

        if self.verbose:
            print(f"  Added {total_edges} new edges")

        # Simplify (skips automatically when embeddings=None)
        if self.verbose:
            print("  Simplifying graph...")
        merge_map = simplify_graph(self.builder, None, verbose=self.verbose)

        # Generate summaries
        if not self.skip_summaries and anthropic_client:
            if self.verbose:
                print("  Generating summaries...")
            generate_summaries(
                self.builder, anthropic_client,
                model=self.summary_model, verbose=self.verbose,
            )

        # Generate codemap
        from .codemap import generate_codemap
        generate_codemap(self.builder, cache_dir=self._cache_dir)

        # Save state
        self._save_state()

        stats = self.builder.stats()
        stats["merged_nodes"] = len(merge_map)
        stats["files_indexed"] = len(self._manifest)
        return stats

    # ---- querying (v3: dispatch-based) -------------------------------------

    def query(
        self,
        query: str,
        depth: int = 2,
        max_results: int = 20,
        edge_types: list[str] | None = None,
    ) -> dict:
        """Run a query through the tiered dispatch system.

        Returns a RetrievalPlan as a dict.
        """
        from .retrieval.dispatch import dispatch
        from .retrieval.plan import format_text

        plan = dispatch(
            query=query,
            builder=self.builder,
            depth=depth,
            max_results=max_results,
            edge_types=edge_types,
        )
        result = plan.to_dict()
        result["context_text"] = format_text(plan)
        return result

    # LEGACY: kept for backward compatibility with old MCP tools
    def find_path(
        self,
        source: str,
        target: str,
        k_paths: int = 3,
    ) -> dict:
        """Find paths between two entities. (Legacy — uses old pathfinder)"""
        from .retrieval.pathfinder import find_paths
        paths = find_paths(
            source=source,
            target=target,
            builder=self.builder,
            k_paths=k_paths,
        )
        return {
            "source": source,
            "target": target,
            "edge_paths": [p.to_dict() for p in paths],
            "num_paths": len(paths),
        }

    # LEGACY: kept for backward compatibility
    def get_neighbors(self, node: str, s: int = 1) -> dict:
        """Get edge-intersection neighbourhood for a node. (Legacy)"""
        incident = self.builder.get_edges_for_node(node)
        intersecting: list[dict] = []

        seen: set[str] = set()
        for edge in incident:
            adjacent = self.builder.get_adjacent_edges(edge.edge_id, s=s)
            for adj_eid, intersection_nodes in adjacent:
                if adj_eid in seen:
                    continue
                seen.add(adj_eid)
                adj_record = self.builder.get_edge(adj_eid)
                if adj_record:
                    intersecting.append({
                        "edge_id": adj_eid,
                        "intersects_with": edge.edge_id,
                        "intersection_nodes": sorted(intersection_nodes),
                        "relation": adj_record.relation,
                        "edge_type": adj_record.edge_type,
                    })

        return {
            "resolved_node": node,
            "incident_edges": [e.to_dict() for e in incident],
            "intersecting_edges": intersecting,
        }

    # LEGACY: kept for backward compatibility
    def get_coverage(
        self,
        retrieved_edge_ids: list[str],
        seed_node_ids: list[str],
        depth: int = 1,
    ) -> dict:
        """Evaluate coverage of retrieved edges. (Legacy)"""
        from .retrieval.coverage import evaluate_coverage
        result = evaluate_coverage(
            retrieved_edge_ids=retrieved_edge_ids,
            seed_node_ids=seed_node_ids,
            builder=self.builder,
            depth=depth,
        )
        return result.to_dict()

    def stats(self) -> dict:
        return self.builder.stats()

    def list_nodes(self, limit: int = 100) -> list[dict]:
        """List all nodes with degree info."""
        nodes = sorted(self.builder.get_all_nodes())
        result = []
        for node in nodes[:limit]:
            result.append({
                "node": node,
                "degree": self.builder.get_node_degree(node),
            })
        return sorted(result, key=lambda x: x["degree"], reverse=True)

    def list_edges(self, limit: int = 100, edge_type: str | None = None) -> list[dict]:
        """List all edges with metadata."""
        edges = list(self.builder._edge_store.values())
        if edge_type:
            edges = [e for e in edges if e.edge_type == edge_type]
        edges = edges[:limit]
        return [e.to_dict() for e in edges]

    # ---- state persistence -------------------------------------------------

    def _save_state(self) -> None:
        if self._cache_dir is None:
            return
        self.builder.save(self._cache_dir / "builder.pkl")
        with open(self._cache_dir / "manifest.json", "w") as f:
            json.dump(self._manifest, f, indent=2)

    def _load_state(self) -> None:
        if self._cache_dir is None:
            return

        builder_path = self._cache_dir / "builder.pkl"
        manifest_path = self._cache_dir / "manifest.json"

        if builder_path.exists():
            self.builder = HypergraphBuilder.load(builder_path)
            if self.verbose:
                print("  Loaded existing graph")
        if manifest_path.exists():
            with open(manifest_path) as f:
                self._manifest = json.load(f)
            if self.verbose:
                print(f"  Loaded manifest: {len(self._manifest)} files")

    def save(self, path: str | Path) -> None:
        """Save full state to a directory."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self.builder.save(path / "builder.pkl")
        with open(path / "manifest.json", "w") as f:
            json.dump(self._manifest, f, indent=2)

    def load(self, path: str | Path) -> None:
        """Load full state from a directory."""
        path = Path(path)
        self.builder = HypergraphBuilder.load(path / "builder.pkl")
        manifest_path = path / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                self._manifest = json.load(f)


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
