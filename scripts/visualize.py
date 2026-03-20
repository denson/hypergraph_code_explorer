#!/usr/bin/env python3
"""Visualize the hypergraph structure."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hypergraph_code_explorer.pipeline import HypergraphPipeline


def main():
    cache_dir = sys.argv[1] if len(sys.argv) > 1 else ".hce_cache"

    pipeline = HypergraphPipeline()
    pipeline.load(cache_dir)

    stats = pipeline.stats()
    print("=== Graph Statistics ===")
    print(json.dumps(stats, indent=2))

    print("\n=== Top 20 Nodes by Degree ===")
    nodes = pipeline.list_nodes(limit=20)
    for n in nodes:
        print(f"  {n['node']:40s}  degree={n['degree']}")

    print("\n=== Edge Type Breakdown ===")
    for etype, count in stats.get("edge_type_counts", {}).items():
        print(f"  {etype:15s}  {count}")


if __name__ == "__main__":
    main()
