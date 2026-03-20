#!/usr/bin/env python3
"""Quick smoke test: index a directory and run a query."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hypergraph_code_explorer.pipeline import HypergraphPipeline


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "."

    pipeline = HypergraphPipeline(verbose=True, skip_summaries=True)
    stats = pipeline.index_directory(target)
    print(f"\nStats: {stats}")

    if stats["num_edges"] > 0:
        result = pipeline.query("how does the main function work")
        print(f"\nQuery result preview:")
        print(result.get("context_text", "")[:1000])


if __name__ == "__main__":
    main()
