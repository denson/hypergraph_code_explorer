#!/usr/bin/env python3
"""Query an indexed codebase."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hypergraph_code_explorer.pipeline import HypergraphPipeline


def main():
    if len(sys.argv) < 2:
        print("Usage: python query.py <query> [--cache-dir <path>]")
        sys.exit(1)

    query = sys.argv[1]
    cache_dir = ".hce_cache"
    if "--cache-dir" in sys.argv:
        idx = sys.argv.index("--cache-dir")
        cache_dir = sys.argv[idx + 1]

    pipeline = HypergraphPipeline(verbose=True)
    pipeline.load(cache_dir)

    result = pipeline.query(query)
    print(result.get("context_text", ""))


if __name__ == "__main__":
    main()
