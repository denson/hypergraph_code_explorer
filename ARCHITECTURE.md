# Hypergraph Code Explorer — Architecture Guide

## Overview

`hypergraph_code_explorer` is a Python package that ingests a codebase, extracts structural relationships as **hyperedges** (N-ary relationships, not pairwise), and exposes 8 MCP tools for an AI agent to explore the resulting graph.

The core differentiator: **edge-intersection traversal**. Two hyperedges are connected when they share member nodes, and those shared nodes explain *why* the connection exists.

## Quick Start

```bash
# Install
uv sync

# Set up API key (needed for summaries)
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY

# Index a codebase
hce index ../requests --verbose

# Query
hce query "how does session send work"

# Start MCP server
hce server
```

## Architecture

### Data Flow

```
Source Files → Converter → Chunker → Extractor → Builder → Embeddings → Simplify → Summaries
                                                    ↓
                                             Inverted Index
                                                    ↓
                                    Retrieval (intersection traversal)
                                                    ↓
                                         MCP Tools (8 endpoints)
```

### Core Data Structures

**HyperedgeRecord** — A directed hyperedge:
- `sources`: subject entities (caller, importer, definer)
- `targets`: object entities (callee, imported module, defined members)
- `all_nodes`: sources ∪ targets (used for intersection math)
- `edge_type`: CALLS, IMPORTS, DEFINES, INHERITS, SIGNATURE, RAISES, DECORATES, TEXT, SUMMARY

**HypergraphBuilder** — The graph store:
- `_incidence`: edge_id → set of nodes
- `_node_to_edges`: node → set of edge_ids (inverted index, maintained on every insert)
- `_edge_store`: edge_id → HyperedgeRecord

The inverted index makes intersection traversal fast: O(1) to find all edges touching a node.

### Edge Types

| Type | Source | Weight | Description |
|------|--------|--------|-------------|
| CALLS | AST | 1.0 | Function/method call sites |
| IMPORTS | AST | 1.0 | Import statements |
| DEFINES | AST | 1.0 | Class/function definitions |
| INHERITS | AST | 1.0 | Class inheritance |
| SIGNATURE | AST | 1.0 | Function parameter types |
| RAISES | AST | 1.0 | Exception types raised |
| DECORATES | AST | 1.0 | Decorator usage |
| TEXT | LLM | 0.7 | Semantic relationships (opt-in) |
| SUMMARY | LLM | 0.3 | File-level summaries |

### Retrieval Algorithm

**Phase 1 — Seed Selection:**
1. Embed query → find top-K similar nodes
2. Collect all edges incident on those nodes
3. Score: `(α × weighted_precision + (1−α) × coverage) × type_weight`

**Phase 2 — Intersection Expansion:**
4. For each seed edge, find edges sharing ≥ s nodes
5. Score by intersection quality
6. Tag as `retrieval_source = "intersection"`

**Phase 3 — Traversal Path Construction:**
7. Follow highest-scoring intersections from seed edges
8. Record intersection nodes at each hop (conceptual bridges)

### Scoring

```
score = (α × weighted_precision + (1−α) × coverage) × type_weight

weighted_precision = Σ(similarity of matched nodes) / |edge_nodes|
coverage = count(matched nodes in edge) / K
α = 0.6 (default, configurable)
```

Additive, not harmonic mean (F1). No U-shaped valley for peripheral entities.

### Node Simplification

After building, merge nodes with cosine similarity > 0.97. Merge lower-degree into higher-degree. Code identifiers are precise — the high threshold prevents merging distinct names like `send` and `send_request`.

### File-Hash Caching

Manifest: `{file_path: (sha256, [edge_ids])}`. On re-index: skip unchanged, re-extract modified, add new, remove deleted.

## MCP Tools

### 1. `hypergraph_retrieve(query, top_k=20, alpha=0.6)`
Main retrieval. Returns edges with traversal paths and context text.

### 2. `hypergraph_find_path(source, target, k_paths=3)`
Edge-BFS between two entities. Returns paths with intersection explanations.

### 3. `hypergraph_neighbors(node, s=1)`
Edge-intersection neighbourhood of a node.

### 4. `hypergraph_coverage(retrieved_edge_ids, seed_node_ids, depth=1)`
Agent self-evaluation. No LLM calls, purely graph-structural.

### 5. `hypergraph_summarize(scope, paths, force, model)`
Generate file-level summaries. Build-time operation.

### 6. `hypergraph_stats()`
Node count, edge count, type breakdown.

### 7. `hypergraph_list_nodes(limit=100)`
All nodes sorted by degree.

### 8. `hypergraph_list_edges(limit=100, edge_type=None)`
All edges with metadata.

## Module Dependency Order

```
models.py              ← no deps
graph/builder.py       ← models
graph/embeddings.py    ← numpy
graph/simplify.py      ← builder, embeddings
graph/summaries.py     ← builder, anthropic
ingestion/converter.py ← markitdown
ingestion/chunker.py   ← ast, converter
extraction/code_extractor.py ← ast, models, chunker
extraction/text_extractor.py ← anthropic, instructor, models
retrieval/intersection.py    ← builder, embeddings, models
retrieval/pathfinder.py      ← builder, models
retrieval/context.py         ← models
retrieval/coverage.py        ← builder, models
pipeline.py            ← all above
api.py                 ← pipeline
mcp_server.py          ← api, mcp
cli.py                 ← pipeline
```
