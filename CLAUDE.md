# Hypergraph Code Explorer — Implementation Instructions

## What You Are Building

A Python package called `hypergraph_code_explorer` that provides hypergraph-based code understanding via an MCP server. It ingests a codebase, extracts structural relationships (function calls, imports, class definitions, etc.) as hyperedges (N-ary relationships, not pairwise), and exposes 8 MCP tools for an AI agent to explore the resulting graph.

The core differentiator over standard GraphRAG is **edge-intersection traversal**: two hyperedges are connected when they share member nodes, and those shared nodes explain *why* the connection exists. This is fundamentally richer than binary entity-relationship graphs.

## Architecture Plan

**Read `docs/hypergraph_rag_v2_plan.md` FIRST.** It is the authoritative design document. All decisions are resolved — implement exactly what it specifies. Do not deviate from the plan unless you encounter a technical impossibility, in which case document the deviation in a code comment explaining why.

## Reference Code

Two existing codebases are available for reference. **Adapt patterns from these — do not copy-paste without understanding and modifying for v2's directed edge model.**

### v1 (our previous implementation)
Location: `../hypergraph_rag/src/hypergraph_rag/`

Carry forward with modifications:
- `ingestion/document_converter.py` → rename to `converter.py`, add skip_hidden logic (skip `.git`, `__pycache__`, `node_modules`, `.venv`)
- `ingestion/chunker.py` → carry forward, AST-boundary splitting works well
- `extraction/code_extractor.py` → carry forward, but populate `sources` and `targets` (directed) instead of flat `nodes` set. Source = subject entity (caller, importer, definer), target = object entity (callee, imported module, defined members)
- `extraction/text_extractor.py` → carry forward, same directional change. Remember: TEXT edges are OPT-IN (disabled by default)
- `graph/embeddings.py` → rewrite. Switched from CodeBERT to `all-MiniLM-L6-v2` (384-dim, trained for STS). CodeBERT's mean-pooling on short identifiers produced degenerate embeddings. Node names are embedded with source-file context prepended (e.g. `"sessions.py: Session.send"`)
- `graph/builder.py` → heavily rewrite. v1 had implicit inverted index; v2 needs explicit `_node_to_edges: dict[str, set[str]]` maintained at insert time
- `mcp_server.py` → rewrite for 8 tools (v1 had 6). Use FastMCP
- `pipeline.py` → rewrite for file-hash caching and summary generation step

Do NOT carry forward:
- `retrieval/search.py` → replaced entirely by `retrieval/intersection.py`
- `query/engine.py` → replaced by `retrieval/context.py`

### MIT HyperGraphReasoning
Location: `../HyperGraphReasoning/GraphReasoning/`

Key patterns to adapt:
- `graph_tools.py` has `find_shortest_path_hypersubgraph_between_nodes_local` — this is the edge-BFS algorithm to adapt for `retrieval/pathfinder.py`
- `graph_analysis.py` has `simplify_hypergraph` — adapt for `graph/simplify.py`
- `graph_generation.py` has incidence dict construction patterns — reference for `graph/builder.py`

## All Design Decisions (Resolved)

Implement these exactly:

| Decision | Value | Rationale |
|----------|-------|-----------|
| Scoring formula | `(α × wp + (1−α) × cov) × type_weight` | Additive, not harmonic mean. No U-shaped valley for peripheral entities |
| Default α | 0.6 | Configurable per query call |
| Default top_k | 20 | Recall-first: generous retrieval, agent filters noise |
| AST intersection threshold s | 1 | Precise identifiers — any shared name is a real connection |
| TEXT intersection threshold s | 2 | Vague noun phrases — require 2 shared concepts |
| TEXT edges | Opt-in (`text_edges: true` in config) | Disabled by default for code repos |
| Node simplification threshold | 0.97 cosine similarity | Higher than MIT's 0.90; code identifiers are precise |
| Summary model | Haiku default, Sonnet via `--model` flag | File-level only, directory roll-ups deferred |
| SUMMARY type_weight | 0.3 | Below structural edges (AST=1.0, TEXT=0.7) |
| Coverage check trigger | `coverage_score < 0.5` OR `frontier node incident_edge_count > 5` | |
| Embeddings | `all-MiniLM-L6-v2` | 384-dim, trained for STS. Replaced CodeBERT (degenerate embeddings on short identifiers). Context-prefixed node names |

## Project Structure

Create this exact structure. The package name is `hypergraph_code_explorer` (not `hypergraph_rag`).

```
hypergraph_code_explorer/
├── pyproject.toml
├── .env.example                    # ANTHROPIC_API_KEY=sk-ant-...
├── .gitignore
├── CLAUDE.md                       # this file
├── ARCHITECTURE.md                 # write after Phase 4
├── docs/
│   └── hypergraph_rag_v2_plan.md   # the architecture plan (already present)
├── .cursor/
│   ├── mcp.json
│   └── rules/
│       └── hypergraph.mdc
├── src/
│   └── hypergraph_code_explorer/
│       ├── __init__.py
│       ├── models.py
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── converter.py
│       │   └── chunker.py
│       ├── extraction/
│       │   ├── __init__.py
│       │   ├── code_extractor.py
│       │   └── text_extractor.py
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── builder.py
│       │   ├── embeddings.py
│       │   ├── simplify.py
│       │   └── summaries.py
│       ├── retrieval/
│       │   ├── __init__.py
│       │   ├── intersection.py
│       │   ├── pathfinder.py
│       │   ├── context.py
│       │   └── coverage.py
│       ├── pipeline.py
│       ├── api.py
│       ├── mcp_server.py
│       └── cli.py
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_builder.py
│   ├── test_code_extractor.py
│   ├── test_intersection.py
│   ├── test_pathfinder.py
│   ├── test_coverage.py
│   └── test_pipeline.py
└── scripts/
    ├── test_pipeline.py
    ├── query.py
    └── visualize.py
```

## pyproject.toml

```toml
[project]
name = "hypergraph-code-explorer"
version = "0.1.0"
description = "Hypergraph-based code exploration with edge-intersection traversal, exposed as an MCP server"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.46.0",
    "python-dotenv>=1.0.0",
    "hypernetx>=2.4.0",
    "networkx>=3.0",
    "numpy>=1.26.0",
    "sentence-transformers>=3.0.0",
    "markitdown[all]>=0.1.0",
    "pydantic>=2.0.0",
    "instructor>=1.0.0",
    "langchain-text-splitters>=0.3.0",
    "mcp>=1.0.0",
]

[project.scripts]
hce = "hypergraph_code_explorer.cli:main"
hce-server = "hypergraph_code_explorer.mcp_server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/hypergraph_code_explorer"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

## Implementation Order

Follow this strictly. Each step's dependencies are satisfied by prior steps.

### Phase 1 — Core data structures and graph building

**Step 1: `models.py`**
All dataclasses in one file. See §1 and §4 of the v2 plan. Must include:
- `EdgeType` enum: CALLS, IMPORTS, DEFINES, INHERITS, SIGNATURE, RAISES, DECORATES, TEXT, SUMMARY
- `DEFAULT_TYPE_WEIGHTS` dict: AST=1.0, TEXT=0.7, SUMMARY=0.3
- `DEFAULT_INTERSECTION_THRESHOLDS` dict: AST=1, TEXT=2, SUMMARY=1
- `HyperedgeRecord`: edge_id, relation, edge_type, sources (list), targets (list), all_nodes (set = sources ∪ targets), source_path, chunk_id, chunk_text, metadata (dict)
- `TraversalHop`: from_edge, to_edge, intersection_nodes, from_members, to_members
- `PathReport`: edges (list of edge_ids), hops (list of TraversalHop), start_comembers, end_comembers
- `ScoredEdge`: edge (HyperedgeRecord), weighted_precision, coverage, score, retrieval_source ("seed"/"intersection"), matched_nodes
- `RetrievalResult`: query, matched_nodes, scored_edges, traversal_paths, coverage_score, intersection_density, retrieval_source_breakdown
- `CoverageResult`: covered_nodes, uncovered_nodes, frontier_nodes, coverage_score, intersection_density, retrieval_source_breakdown

**Step 2: `graph/builder.py`**
See §2 of the plan. Core data structure:
```python
class HypergraphBuilder:
    _incidence: dict[str, set[str]]       # edge_id → node set
    _node_to_edges: dict[str, set[str]]   # node → set of edge_ids (INVERTED INDEX)
    _edge_store: dict[str, HyperedgeRecord]
    _chunk_registry: dict[str, str]       # chunk_id → chunk_text
```
Methods: `add_edge(record)`, `get_edges_for_node(node)`, `get_intersection(edge_id_1, edge_id_2)`, `get_adjacent_edges(edge_id, s=1)` (edges sharing ≥ s nodes), `remove_edges_by_file(source_path)`, `serialize()`, `deserialize()`.
The inverted index `_node_to_edges` MUST be maintained on every `add_edge` call — this is what makes intersection traversal fast (O(1) node → edges lookup).

**Step 3: `ingestion/converter.py`**
Adapt from v1's `document_converter.py`. Uses markitdown for non-Python files. Must skip hidden directories: `.git`, `__pycache__`, `node_modules`, `.venv`, `venv`, `.eggs`, `dist`, `build`. Walk the target directory, discover files, convert each to text.

**Step 4: `ingestion/chunker.py`**
Adapt from v1. Two strategies:
- Python files: split on AST boundaries (functions, classes, top-level statements)
- Non-Python: split on headings/paragraphs using langchain-text-splitters
Each chunk gets a unique `chunk_id` and the raw text.

**Step 5: `extraction/code_extractor.py`**
Adapt from v1. Uses Python's `ast` module (zero LLM cost). Extract these edge types:
- CALLS: function/method call sites. Source = caller, target = callee
- IMPORTS: import statements. Source = importing file, target = imported module/name
- DEFINES: class/function definitions. Source = container (class/module), target = defined members
- INHERITS: class inheritance. Source = subclass, target = base class
- SIGNATURE: function signatures. Source = function, target = parameter types/names
- RAISES: raise/except statements. Source = function, target = exception class
- DECORATES: decorator usage. Source = decorator, target = decorated function/class

Each extraction produces a `HyperedgeRecord` with populated `sources`, `targets`, and `all_nodes`.

**Step 6: `extraction/text_extractor.py`**
Adapt from v1. Uses Claude (via anthropic + instructor) to extract semantic relationships from docstrings and comments. Produces TEXT edges with S-V-O structure.
**IMPORTANT**: This is OPT-IN. Guard with a config flag. Do not run by default.

**Step 7: `graph/embeddings.py`**
Rewritten. Uses `all-MiniLM-L6-v2` via sentence-transformers (replaced CodeBERT — see Key Decisions).
- `embed_nodes(nodes: list[str]) -> np.ndarray` — batch embed node names
- `embed_query(query: str) -> np.ndarray` — embed a single query string
- `cosine_similarity(a, b) -> float`
- `find_top_k(query_embedding, node_embeddings, k) -> list[tuple[str, float]]`
No `trust_remote_code=True`. No task prefixes.

### Phase 2 — The retrieval core

**Step 8: `retrieval/intersection.py`** — THE CORE MODULE
This is the most important file. Implements the full retrieval algorithm from §4 of the plan:

Phase 1 — Seed selection:
1. Embed query → find top-K similar nodes
2. Collect all edges incident on those nodes (using builder's inverted index)
3. Score each seed edge: `score = (α × wp + (1−α) × cov) × type_weight`
   - `wp = Σ(sim of matched nodes in edge) / |edge.all_nodes|`
   - `cov = count(matched nodes in edge) / K`
4. Tag `retrieval_source = "seed"`

Phase 2 — Intersection expansion:
5. For each top seed edge, find adjacent edges (share ≥ s nodes, s per edge type)
6. Score by intersection quality: `|intersection_nodes| × avg(sim of intersection_nodes)`
7. Tag `retrieval_source = "intersection"`
8. Combine: `final = α × seed_score + (1−α) × intersection_score`

Phase 3 — Traversal path construction:
9. Build traversal paths following highest-scoring intersections
10. Record intersection_nodes at each hop

Return a `RetrievalResult`.

**Step 9: `retrieval/pathfinder.py`**
Edge-BFS algorithm from §5. Adapt from MIT's `find_shortest_path_hypersubgraph_between_nodes_local` in `../HyperGraphReasoning/GraphReasoning/graph_tools.py`.
- Input: source entity, target entity, builder, k_paths
- BFS through edge space: two edges are adjacent if they share ≥ s nodes
- Return up to k_paths `PathReport` objects with intersection nodes at each hop

**Step 10: `retrieval/context.py`**
Context assembly from §6. Takes a `RetrievalResult` and produces structured text:
```
=== RETRIEVAL SUMMARY ===
Query: "..."
Coverage score: 0.61
Uncovered frontier nodes: [AuthBase, HTTPDigestAuth]
Edge provenance: 5 seed, 3 intersection

=== Traversal Path 1 ===
[Edge 1] CALLS: Session.send → HTTPAdapter.send  [seed | score: 0.87 | HIGH]
  Source: requests/sessions.py
  ---
  <chunk_text>
  ---
  ↓ connected via: {HTTPAdapter, poolmanager}
[Edge 2] DEFINES: HTTPAdapter members  [intersection | score: 0.72 | MED]
  ...
```

Confidence tiers: HIGH (score ≥ 0.7), MED (0.4–0.7), LOW (< 0.4).

**Step 11: `retrieval/coverage.py`**
The `hypergraph_coverage` tool from §8. Purely local, no LLM calls. Input: retrieved_edge_ids, seed_node_ids, depth. Output: CoverageResult with covered_nodes, uncovered_nodes, frontier_nodes (with incident_edge_count and suggested_query), coverage_score, intersection_density.

### Phase 3 — Pipeline and interface

**Step 12: `graph/simplify.py`**
Node simplification from §10. After building, merge nodes with cosine similarity > 0.97. Merge lower-degree into higher-degree. Update incidence dict, edge store, inverted index, embeddings in one pass.

**Step 13: `graph/summaries.py`**
Module-level summaries from §9. Group edges by source_path, generate 2-3 sentence summary per file using Anthropic API (Haiku default). Store as SUMMARY edge type. Extract 3-5 key entity names for the all_nodes field.

**Step 14: `pipeline.py`**
Orchestrator. Sequence: discover files → convert → chunk → extract edges → build graph → embed → simplify → generate summaries. File-hash caching (§11): store manifest `{file_path: (sha256, [edge_ids])}`. On re-index: skip unchanged, re-extract modified, add new, remove deleted.

**Step 15: `api.py`**
`HypergraphSession` — serializable API layer between pipeline and MCP server. Wraps pipeline operations into methods matching the 8 MCP tools. Handles serialization of results to JSON-compatible dicts.

**Step 16: `mcp_server.py`**
FastMCP server exposing 8 tools:
1. `hypergraph_retrieve` — main retrieval with traversal paths
2. `hypergraph_find_path` — edge-BFS between two entities
3. `hypergraph_neighbors` — edge-intersection neighbourhood expansion
4. `hypergraph_coverage` — agent self-evaluation (no LLM)
5. `hypergraph_summarize` — trigger summary generation (build-time)
6. `hypergraph_stats` — graph statistics
7. `hypergraph_list_nodes` — list all nodes with degree info
8. `hypergraph_list_edges` — list all edges with metadata

See the MCP Tool Interface Changes section of the plan for exact input/output schemas.

**Step 17: `cli.py`**
argparse with subcommands:
- `hce index <path> [--text-edges] [--skip-summaries] [--summary-model haiku|sonnet]`
- `hce query <query> [--top-k 20] [--alpha 0.6]`
- `hce server` — start MCP server

### Phase 4 — Agent integration

**Step 18: `.cursor/rules/hypergraph.mdc`**
See the Cursor Rules Update section of the plan. Teach the three-stage retrieve/evaluate/synthesise loop plus SUMMARY edge awareness.

**Step 19: `.cursor/mcp.json`**
MCP server configuration pointing to `hce-server`.

**Step 20: `ARCHITECTURE.md`**
Comprehensive user guide explaining the hypergraph approach, how to index a codebase, and how to use each tool.

### Phase 5 — Validation

**Step 21-24**: The psf/requests test codebase is at `../requests/`. Index it and verify:
- Edge-BFS paths include meaningful intersection explanations
- Broad queries ("how does authentication work?") hit SUMMARY edges
- Coverage tool correctly identifies frontier nodes
- Additive scoring preserves peripheral-but-important edges that F1 would crush

## Module Dependency Order

```
models.py              ← no deps (pure dataclasses + enums)
graph/builder.py       ← models
graph/embeddings.py    ← sentence-transformers, numpy, models
graph/simplify.py      ← builder, embeddings
graph/summaries.py     ← builder, anthropic
ingestion/converter.py ← markitdown
ingestion/chunker.py   ← ast, langchain-text-splitters
extraction/code_extractor.py ← ast (stdlib), models
extraction/text_extractor.py ← anthropic, instructor, models
retrieval/intersection.py    ← builder, embeddings, models
retrieval/pathfinder.py      ← builder, models
retrieval/context.py         ← models
retrieval/coverage.py        ← builder, models
pipeline.py            ← all above
api.py                 ← pipeline, models
mcp_server.py          ← api, mcp
cli.py                 ← pipeline
```

## Testing

Write unit tests alongside each module. Use pytest. Key test scenarios:

- `test_models.py` — dataclass construction, all_nodes auto-population, serialization round-trips
- `test_builder.py` — add_edge maintains inverted index, get_adjacent_edges returns correct pairs at different s thresholds, remove_edges_by_file cleans up correctly
- `test_code_extractor.py` — extract from a small Python snippet, verify CALLS/IMPORTS/DEFINES edges with correct source/target directionality
- `test_intersection.py` — additive scoring with known edge sets. Use this test data (from the presentation):
  ```python
  # 5 AST edges from the requests library
  AST_edges = [
      {"nodes": ["Session", "HTTPAdapter", "poolmanager", "send"]},       # e1
      {"nodes": ["HTTPAdapter", "poolmanager", "send_request"]},          # e2
      {"nodes": ["Session", "PreparedRequest", "send"]},                  # e3
      {"nodes": ["poolmanager", "ConnectionPool", "urlopen"]},            # e4
      {"nodes": ["send", "HTTPAdapter", "timeout"]},                      # e5
  ]
  # At s=1: 7 of 10 pairs connected
  # At s=2: 3 of 10 pairs connected
  # At s=3: 0 of 10 pairs connected
  ```
- `test_pathfinder.py` — edge-BFS finds paths with correct intersection nodes
- `test_coverage.py` — frontier node detection, coverage score calculation
- `test_pipeline.py` — end-to-end index + query on a small fixture directory

## Code Style

- Python 3.11+ features (type unions with `|`, `match` statements where appropriate)
- Type hints on ALL function signatures
- Pydantic for validation at API/MCP boundaries, dataclasses for internal structures
- Google-style docstrings on public functions
- No star imports
- Use `from __future__ import annotations` in all files

## Environment Setup

```bash
# In the project directory:
uv sync                    # install dependencies
cp .env.example .env       # add your ANTHROPIC_API_KEY
pytest                     # run tests
hce index ../requests      # index the test codebase
hce query "how does session send work"  # test a query
```

## Git

Initialize a git repo in this directory. Make commits at logical milestones — at minimum one commit per phase. Use descriptive commit messages that reference the relevant plan section.
