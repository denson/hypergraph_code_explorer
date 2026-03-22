#!/usr/bin/env python3
"""
Extract graph data from an HCE builder.pkl for D3.js visualization.

Usage:
    python extract_graph.py <path-to-.hce_cache-dir> <output.json>

Produces a JSON file with:
  - nodes: [{id, label, group, degree, importance, language}, ...]
  - edges: [{source, target, type, file}, ...]

The importance score = 2 * (calls_degree + inherits_degree) + total_degree.
Structural edge types only: DEFINES, CALLS, INHERITS, DECORATES, RAISES.
IMPORTS and SIGNATURE are excluded since they add noise without structural value.
"""

import json
import pickle
import sys
import types
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Flexible EdgeType mock — needed to unpickle HCE builder.pkl files
# that were pickled with the real EdgeType enum. The mock accepts any
# string value without raising ValueError.
# ---------------------------------------------------------------------------

class FlexEdgeType:
    """Stand-in for EdgeType enum values."""
    def __init__(self, value):
        self.value = value
    def __repr__(self):
        return f"EdgeType.{self.value}"
    def __str__(self):
        return self.value
    def __eq__(self, other):
        if isinstance(other, FlexEdgeType):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return False
    def __hash__(self):
        return hash(self.value)


class EdgeTypeMeta(type):
    """Metaclass that makes EdgeType('CALLS') return a FlexEdgeType."""
    _cache = {}
    def __call__(cls, value):
        if value not in cls._cache:
            cls._cache[value] = FlexEdgeType(value)
        return cls._cache[value]
    def __getattr__(cls, name):
        return FlexEdgeType(name)


class EdgeType(metaclass=EdgeTypeMeta):
    pass


# Patch the module namespace so pickle finds our mock.
# The HCE pickle references hypergraph_code_explorer.models.EdgeType.
# We create the full module hierarchy so pickle.load() resolves it.
for mod_name in [
    "hypergraph_code_explorer",
    "hypergraph_code_explorer.models",
    "hypergraph_code_explorer.graph",
    "hypergraph_code_explorer.graph.builder",
]:
    sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules["hypergraph_code_explorer.models"].EdgeType = EdgeType


# ---------------------------------------------------------------------------
# Group assignment — maps node IDs to semantic groups based on prefixes
# and file paths. Customize the PREFIX_GROUPS and FILE_GROUPS for each
# codebase, or leave defaults for a reasonable auto-grouping.
# ---------------------------------------------------------------------------

def assign_group(node_id, file_path="", prefix_groups=None, file_groups=None):
    """Assign a semantic group to a node based on its ID prefix or file path."""
    if prefix_groups:
        for prefix, group in prefix_groups.items():
            if node_id.startswith(prefix):
                return group

    if file_groups and file_path:
        for pattern, group in file_groups.items():
            if pattern in file_path:
                return group

    # Fallback: use the top-level module name
    parts = node_id.split(".")
    if len(parts) > 1:
        return parts[0]
    return "other"


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

STRUCTURAL_TYPES = {"DEFINES", "CALLS", "INHERITS", "DECORATES", "RAISES"}

# Map file extensions to language names for the visualization
EXT_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
}


def detect_language(file_path):
    """Detect language from file extension."""
    if not file_path:
        return "other"
    ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    return EXT_TO_LANGUAGE.get(ext.lower(), "other")


def extract_graph(cache_dir, prefix_groups=None, file_groups=None):
    """Load builder.pkl and extract graph data.

    The pickle format is a dict with keys:
      - 'edge_store': dict of edge_id -> edge record (dict or HyperedgeRecord)
      - 'incidence': dict of edge_id -> set of node names
      - 'chunk_registry': dict of chunk_id -> chunk text
    """
    pkl_path = Path(cache_dir) / "builder.pkl"
    if not pkl_path.exists():
        print(f"Error: {pkl_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(pkl_path, "rb") as f:
        data = pickle.load(f)

    # Handle both dict-based and class-based pickle formats
    if isinstance(data, dict):
        edge_store = data.get("edge_store", {})
    elif hasattr(data, "_edge_store"):
        edge_store = data._edge_store
    else:
        print(f"Error: unexpected pickle format: {type(data)}", file=sys.stderr)
        sys.exit(1)

    # Collect edges and node degrees
    degree = defaultdict(int)
    calls_degree = defaultdict(int)
    inherits_degree = defaultdict(int)
    node_files = {}  # node -> first file seen
    edges = []

    for eid, rec in edge_store.items():
        # Handle both dict and object records
        if isinstance(rec, dict):
            etype = str(rec.get("edge_type", ""))
            src_list = rec.get("sources", [])
            tgt_list = rec.get("targets", [])
            source_path = rec.get("source_path", "")
        else:
            etype = str(getattr(rec, "edge_type", ""))
            src_list = getattr(rec, "sources", [])
            tgt_list = getattr(rec, "targets", [])
            source_path = getattr(rec, "source_path", "")

        if etype not in STRUCTURAL_TYPES:
            continue

        for s in src_list:
            for t in tgt_list:
                edges.append({
                    "source": s,
                    "target": t,
                    "type": etype,
                    "file": source_path,
                })
                degree[s] += 1
                degree[t] += 1
                if etype == "CALLS":
                    calls_degree[s] += 1
                    calls_degree[t] += 1
                elif etype == "INHERITS":
                    inherits_degree[s] += 1
                    inherits_degree[t] += 1

                if source_path:
                    if s not in node_files:
                        node_files[s] = source_path
                    if t not in node_files:
                        node_files[t] = source_path

    # Build nodes
    all_node_ids = set()
    for e in edges:
        all_node_ids.add(e["source"])
        all_node_ids.add(e["target"])

    nodes = []
    for nid in sorted(all_node_ids):
        d = degree.get(nid, 0)
        imp = 2 * (calls_degree.get(nid, 0) + inherits_degree.get(nid, 0)) + d
        group = assign_group(nid, node_files.get(nid, ""), prefix_groups, file_groups)
        label = nid.split(".")[-1] if "." in nid else nid
        lang = detect_language(node_files.get(nid, ""))
        nodes.append({
            "id": nid,
            "label": label,
            "group": group,
            "degree": d,
            "importance": imp,
            "language": lang,
        })

    return {"nodes": nodes, "edges": edges}


def main():
    if len(sys.argv) < 3:
        print("Usage: python extract_graph.py <.hce_cache-dir> <output.json>")
        sys.exit(1)

    cache_dir = sys.argv[1]
    output_path = sys.argv[2]

    data = extract_graph(cache_dir)
    with open(output_path, "w") as f:
        json.dump(data, f)

    print(f"Extracted {len(data['nodes'])} nodes, {len(data['edges'])} edges -> {output_path}")


if __name__ == "__main__":
    main()
