# Hypergraph Code Explorer — Architecture Guide

## Overview

`hypergraph_code_explorer` is a Python package that ingests a multi-language codebase, extracts structural relationships as **hyperedges** (N-ary relationships, not pairwise), and provides a tiered retrieval system for AI agents and humans to explore the resulting graph. Extraction uses tree-sitter for 10 languages (Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, PHP) with a regex fallback for unsupported file types.

The core value: **precomputed structural knowledge at zero LLM cost**. The hypergraph gives agents instant access to call chains, inheritance trees, and dependency graphs that would otherwise take many rounds of grep-read-think to discover.

## Architecture

### Data Flow

```
Source Files (.py, .js, .ts, .go, .rs, .java, .c, .cpp, .rb, .php, ...)
     │
     ▼
Converter (markitdown) → Chunker (line-range splitting)
     │
     ▼
Extractor (tree-sitter per-file parsing, regex fallback)
     │
     ▼
Builder (hypergraph construction, inverted index, IDF)
     │
     ▼
Simplify → Summaries (optional, LLM) → Codemap
     │
     ▼
Tiered Retrieval (dispatch)
  ├── Tier 1: Exact Lookup
  ├── Tier 2: Structural Traversal
  ├── Tier 3: Text Search
  └── Tier 4: Semantic Search (optional, embeddings)
     │
     ▼
CLI / MCP Tools (5 endpoints)
     │
     ▼
Visualization Pipeline (optional, via skill)
  extract_graph.py → generate_viz.py → self-contained HTML
```

### Extraction: Tree-Sitter

All supported languages are extracted per-file (not per-chunk) to ensure correct class-qualified names and complete DEFINES edges. The extraction layer has two components:

**`treesitter_extractor.py`** — The core backend. Loads tree-sitter grammars lazily per language, walks the AST, and emits `HyperedgeRecord` objects. Each language maps file extensions to a tree-sitter grammar (e.g., `.tsx` → TSX, `.hpp` → C++). Language detection is by file extension via `LANGUAGE_MAP`.

**`code_extractor.py`** — The public-facing class (`CodeHyperedgeExtractor`). Delegates to `treesitter_extractor` for supported languages and falls back to regex for anything else. The regex fallback extracts only DEFINES and IMPORTS edges — enough for basic structure but no call graph.

**`_legacy_python_extractor.py`** — Deprecated. The original Python-only `ast` module extractor, kept for test backward compatibility.

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
- `compute_node_idf()`: IDF weighting for hub detection
- `get_hub_nodes()`: identifies high-degree "hub" nodes (int, str, etc.)

The inverted index makes intersection traversal fast: O(1) to find all edges touching a node.

**RetrievalPlan** — Structured output from retrieval:
- `primary_files`: files to read, with symbols and reasons
- `grep_suggestions`: patterns to search for
- `related_symbols`: named relationships found in the graph
- `structural_context`: textual explanation of findings
- `tiers_used`: which retrieval tiers contributed results

### Edge Types

| Type | Source | Weight | Description |
|------|--------|--------|-------------|
| CALLS | tree-sitter | 1.0 | Function/method call sites |
| IMPORTS | tree-sitter | 1.0 | Import statements |
| DEFINES | tree-sitter | 1.0 | Class/function definitions |
| INHERITS | tree-sitter | 1.0 | Class inheritance |
| SIGNATURE | tree-sitter | 1.0 | Function parameter types |
| RAISES | tree-sitter | 1.0 | Exception types raised |
| DECORATES | tree-sitter | 1.0 | Decorator usage |
| TEXT | LLM | 0.7 | Semantic relationships (opt-in) |
| SUMMARY | LLM | 0.3 | File-level summaries |

### Tiered Retrieval System

**Tier 1 — Exact Lookup** (`retrieval/lookup.py`):
Microsecond-speed name lookup via the inverted index. Tokenizes the query, matches against node names (case-insensitive, segment-aware), returns all incident edges grouped by type.

**Tier 2 — Structural Traversal** (`retrieval/traverse.py`):
BFS through typed edges from seed nodes. Infers edge types from verbs in the query ("calls" → CALLS, "inherits" → INHERITS). Supports forward/backward/both direction. Hub nodes (>3% of edges) are excluded from traversal to prevent explosion.

**Tier 3 — Text Search** (`retrieval/textsearch.py`):
Substring matching across node names, file paths, and relation strings. Ranks by match quality: exact stem > prefix > substring > relation > path.

**Tier 4 — Semantic Search** (`retrieval/semantic.py`):
Optional embedding-based fallback using `all-MiniLM-L6-v2`. Requires `pip install hce[embed]`. Hybrid keyword/embedding matching with `max(embedding_score, keyword_score)`.

**Dispatcher** (`retrieval/dispatch.py`):
Classifies queries and routes through tiers:
1. If query contains exact node names → Tier 1 + Tier 2
2. If query contains relationship verbs → Tier 2
3. If no exact matches → Tier 3, then feed results back into Tier 1+2
4. Tier 4 available on demand

### Hub Node Filtering

High-degree nodes (`int`, `str`, `isinstance`) pollute traversal results. The builder computes IDF weights: `idf(n) = log(1 + total_edges / degree(n))`. Hub detection uses a hybrid threshold: `min(3% of total edges, floor of 50)`. The percentage adapts to graph size while the floor catches builtins in large graphs where 3% would be too high (e.g., at Django's 19k edges, 3% = 581, missing `len` at 498 edges). Hubs are excluded from adjacency traversal.

### CODEBASE_MAP.md

Generated automatically after indexing. Contains modules (all source files with one-line descriptions), key symbols (top 100 nodes by connectivity), call chains (top 20 longest call paths), inheritance trees (top 10 class hierarchies), and a CLI quick reference.

### Visualization Pipeline

Visualization is unified with memory tours — there is one tour format, and the `hce visualize` CLI produces both an interactive D3 HTML and a markdown report from it.

**`visualization.py`** — The core module. Takes a `HypergraphBuilder` and a list of `MemoryTour` objects, extracts a focused subgraph (1-hop neighborhood of all tour seed nodes), converts tours to the viz template format (auto-assigned colors, symbol highlighting), and injects everything into the D3 template. Also generates a markdown report with tour index tables, step listings, and tag/type breakdowns.

Key functions:
- `select_tours(store, tags, tour_ids)` — filter tours by tag or ID
- `extract_tour_subgraph(builder, tours)` — 1-hop neighborhood extraction with importance scoring and seed-node boosting
- `memory_tours_to_viz(tours, graph_node_ids)` — converts `MemoryTour` objects to the viz format with auto-assigned colors from a 12-color palette
- `generate_html(builder, tours, output_path)` — subgraph extraction + tour conversion + template injection → self-contained HTML
- `generate_report(tours, output_path)` — markdown report in the style of `docs/SAMPLE_TOUR_REPORT.md`
- `generate_visualization(builder, tours, output_base)` — writes both `.html` and `.md`

**`skill/assets/viz_template.html`** — The D3.js rendering template (~580 lines). Includes force-directed layout, importance-based node sizing, dual color modes (by module / by language using GitHub's palette), guided tours with click-to-spotlight, symbol search with suggestion chips, a "Suggest Tours" button (client-side cluster analysis), and a "Copy Prompt for Claude" button. The template uses placeholder markers (`{{TITLE}}`, `// {{DATA_INJECTION}}`, `// {{TOURS_INJECTION}}`, `// {{CONFIG_INJECTION}}`) that `visualization.py` replaces at generation time.

**Superseded scripts** (kept for backward compatibility):
- `skill/scripts/extract_graph.py` + `skill/scripts/generate_viz.py` — the old two-step pipeline that required a separate `tours.json`
- `docs/generate_bug_viz.py` — the one-off bug viz script that proved the unified approach

### Memory Tours

Memory tours are a separate persistence layer for agent-facing architectural notes. Unlike visualization tours (which are authored for the D3 HTML and filter to structural edge types only), memory tours access the **full graph** — all edge types including IMPORTS and SIGNATURE — and carry provenance and reuse metadata.

**Storage**: `.hce_cache/memory_tours.json` — a JSON sidecar next to `builder.pkl`. Never stored inside the graph pickle so graph serialization stays stable.

**Data model** (`memory_tours.py`):
- `MemoryTour` — id, name, summary, keywords, steps, tags, provenance (created_from_query, created_at), promotion flag, usage tracking (use_count, last_used_at)
- `MemoryTourStep` — node (graph node ID), text (narrative), optional file path and edge_type
- `MemoryTourStore` — file-backed CRUD store with filtering by tag and promotion status

**Lifecycle**: Tours are created ephemeral by default, then optionally promoted to persistent memory. The promotion flag lets agents distinguish "working notes" from "durable architectural knowledge."

**Scaffolding**: Two functions derive tours from retrieval results without LLM calls:
- `scaffold_from_plan(plan)` — extracts symbols, files, and relationships from a `RetrievalPlan` into ordered tour steps
- `scaffold_prompt(plan)` — produces a structured prompt payload for LLM-authored tour creation, including the expected JSON schema and existing tour context

**Session API** (`api.py`): `HypergraphSession` exposes `memory_tour_create`, `memory_tour_list`, `memory_tour_get`, `memory_tour_promote`, `memory_tour_remove`, `memory_tour_scaffold_prompt`, `memory_tour_create_from_dict`, and `visualize` for programmatic tour ingestion and visualization.

**Visualization**: Memory tours are the single canonical format for both agent memory and human-readable visualization. `visualization.py` converts tours to the D3 viz format automatically (color assignment, symbol highlighting, subgraph extraction). See the Visualization Pipeline section above.

### File-Hash Caching

Manifest: `{file_path: (sha256, [edge_ids])}`. On re-index: skip unchanged, re-extract modified, add new, remove deleted.

## CLI Commands

| Command | Description |
|---------|-------------|
| `hce index <path>` | Index a codebase (point at the source root, not the repo root) |
| `hce lookup <symbol>` | Look up a symbol (Tier 1+2) |
| `hce search <term>` | Text search (Tier 3) |
| `hce query "question"` | Natural language query (all tiers) |
| `hce overview` | Codebase overview |
| `hce init --tool all` | Generate tool instruction files |
| `hce embed` | Compute embeddings for Tier 4 |
| `hce stats` | Graph statistics |
| `hce tour list\|show\|create\|promote\|remove\|scaffold` | Memory tours — persistent agent-facing architectural notes |
| `hce visualize [--tags t] [--tours id] [-o base]` | Generate D3 HTML + markdown report from memory tours |
| `hce server` | Start MCP server |

All commands support `--json` for structured output.

## MCP Tools

### 1. `hce_lookup(symbol, calls, callers, inherits, imports, depth)`
Look up a symbol. Returns file paths, related symbols, and grep patterns.

### 2. `hce_search(term, max_results)`
Text search across all symbols.

### 3. `hce_query(query, depth)`
Natural language query through all retrieval tiers.

### 4. `hce_overview(top)`
Codebase overview: modules, key symbols, reading order.

### 5. `hce_stats()`
Node count, edge count, type breakdown, hub nodes.

## Module Dependency Order

```
models.py                ← no deps (pure dataclasses + enums)
graph/builder.py         ← models
graph/embeddings.py      ← numpy, sentence-transformers (optional)
graph/simplify.py        ← builder, embeddings
graph/summaries.py       ← builder, anthropic
ingestion/converter.py   ← markitdown
ingestion/chunker.py     ← converter
extraction/treesitter_extractor.py ← tree-sitter, tree-sitter-*, chunker, models
extraction/code_extractor.py       ← treesitter_extractor, chunker, models
extraction/text_extractor.py       ← anthropic, instructor (optional), models
retrieval/plan.py        ← no deps (pure data model)
retrieval/lookup.py      ← builder, plan
retrieval/traverse.py    ← builder, plan
retrieval/textsearch.py  ← builder, plan
retrieval/dispatch.py    ← lookup, traverse, textsearch
retrieval/semantic.py    ← builder, embeddings (optional), plan
codemap.py               ← builder
init.py                  ← no deps
memory_tours.py          ← retrieval/plan (via scaffold), json, pathlib
visualization.py         ← builder, memory_tours, viz_template.html
pipeline.py              ← all above
api.py                   ← builder, retrieval, memory_tours, visualization
mcp_server.py            ← api, mcp
cli.py                   ← all above
skill/scripts/extract_graph.py    ← reads .hce_cache/ JSON directly (superseded)
skill/scripts/generate_viz.py     ← reads graph.json, tours.json (superseded)
```

## Dependencies

Core (always installed):
- `anthropic` — for summaries
- `python-dotenv` — env loading
- `markitdown` — file conversion
- `tree-sitter` — AST parsing framework
- `tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-go`, `tree-sitter-rust`, `tree-sitter-java`, `tree-sitter-c`, `tree-sitter-cpp`, `tree-sitter-ruby`, `tree-sitter-php` — language grammars

Optional extras:
- `[embed]` — `sentence-transformers`, `numpy` (for Tier 4)
- `[server]` — `mcp` (for MCP server mode)
- `[text]` — `pydantic`, `instructor` (for TEXT edge extraction)
- `[all]` — everything
