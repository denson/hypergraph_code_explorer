# Hypergraph Code Explorer — Architecture Guide

## Overview

`hypergraph_code_explorer` is a Python package that ingests a codebase, extracts structural relationships as **hyperedges** (N-ary relationships, not pairwise), and provides a tiered retrieval system for AI agents to explore the resulting graph.

The core value: **precomputed structural knowledge**. The hypergraph gives agents instant access to call chains, inheritance trees, and dependency graphs that would otherwise take 3-5 rounds of grep-read-think to discover.

## Quick Start

```bash
# Install
uv sync

# Set up API key (needed for summaries)
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY

# Index a codebase
hce index ../requests --verbose

# Look up a symbol
hce lookup Session.send --calls

# Search for symbols
hce search "auth"

# Natural language query
hce query "how does session send work"

# Generate tool instruction files
hce init --tool all

# Start MCP server
hce server
```

## Architecture

### Data Flow

```
Source Files → Converter → Chunker → Extractor → Builder → Simplify → Summaries → Codemap
                                                    ↓
                                             Inverted Index
                                                    ↓
                                    Tiered Retrieval (dispatch)
                                         ↓         ↓         ↓         ↓
                                     Tier 1     Tier 2     Tier 3     Tier 4
                                     Lookup    Traverse   TextSearch  Semantic
                                                    ↓
                                          RetrievalPlan output
                                                    ↓
                                      CLI / MCP Tools (5 endpoints)
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
| CALLS | AST | 1.0 | Function/method call sites |
| IMPORTS | AST | 1.0 | Import statements |
| DEFINES | AST | 1.0 | Class/function definitions |
| INHERITS | AST | 1.0 | Class inheritance |
| SIGNATURE | AST | 1.0 | Function parameter types |
| RAISES | AST | 1.0 | Exception types raised |
| DECORATES | AST | 1.0 | Decorator usage |
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

### CODEBASE_MAP.md

Generated automatically after indexing. Contains:
- **Modules**: all source files with one-line descriptions
- **Key Symbols**: top 100 nodes by connectivity
- **Call Chains**: top 20 longest call paths
- **Inheritance Trees**: top 10 class hierarchies
- **CLI Quick Reference**: common hce commands

### Hub Node Filtering

High-degree nodes (`int`, `str`, `isinstance`) pollute traversal results. The builder computes IDF weights: `idf(n) = log(1 + total_edges / degree(n))`. Hub detection uses a hybrid threshold: `min(3% of total edges, floor of 50)`. The percentage adapts to graph size while the floor catches builtins in large graphs where 3% would be too high (e.g., at Django's 19k edges, 3% = 581, missing `len` at 498 edges). Hubs are excluded from adjacency traversal.

### File-Hash Caching

Manifest: `{file_path: (sha256, [edge_ids])}`. On re-index: skip unchanged, re-extract modified, add new, remove deleted.

## CLI Commands

| Command | Description |
|---------|-------------|
| `hce index <path>` | Index a codebase |
| `hce lookup <symbol>` | Look up a symbol (Tier 1+2) |
| `hce search <term>` | Text search (Tier 3) |
| `hce query "question"` | Natural language query (all tiers) |
| `hce overview` | Codebase overview |
| `hce init --tool all` | Generate tool instruction files |
| `hce embed` | Compute embeddings for Tier 4 |
| `hce stats` | Graph statistics |
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
ingestion/chunker.py     ← ast, converter
extraction/code_extractor.py ← ast, models, chunker
extraction/text_extractor.py ← anthropic, instructor (optional), models
retrieval/plan.py        ← no deps (pure data model)
retrieval/lookup.py      ← builder, plan
retrieval/traverse.py    ← builder, plan
retrieval/textsearch.py  ← builder, plan
retrieval/dispatch.py    ← lookup, traverse, textsearch
retrieval/semantic.py    ← builder, embeddings (optional), plan
codemap.py               ← builder
init.py                  ← no deps
pipeline.py              ← all above
api.py                   ← builder, retrieval
mcp_server.py            ← api, mcp
cli.py                   ← all above
```

## Agent Integration

HCE is designed to be used by AI coding agents (Claude Code, Cursor, Cowork,
Codex) as a structural navigation layer. Rather than grepping through files
and hoping to find the right symbols, agents use HCE to ask structured
questions about a codebase and get precise answers with zero LLM cost.

### Installing for Agent Use

```bash
# From the repo root:
pip install -e .

# Verify:
hce --help
```

This puts `hce` on PATH so agents can call it directly.

### Bundled Skill

The `skills/hce-index/` directory contains a ready-to-use skill for Claude Code
and Cowork. The skill teaches the agent to:

1. Index a new codebase when it first encounters one
2. Read the stats to decide if the hypergraph is worth using
3. Use `hce lookup`, `hce search`, and `hce query` before reading source files

**Installing the skill:**

For Claude Code (global):
```bash
cp -r skills/hce-index ~/.claude/skills/
```

For Claude Code (per-project):
```bash
cp -r skills/hce-index .claude/skills/
```

For Cowork, copy to your Cowork skills directory.

### Scale Reference

Tested against real codebases at three scales:

| Codebase | Files | Nodes | Edges | Hub Nodes | Index Time |
|----------|-------|-------|-------|-----------|------------|
| requests | 18 | 906 | 485 | 11 | ~3s |
| FastAPI | 48 | 1,264 | 1,214 | 13 | ~9s |
| Django | 1,163 | 23,614 | 19,382 | 103 | ~196s |

The skill's decision rule: <500 nodes = just read files directly, 500-2000 =
use for targeted lookups, >2000 = essential for efficient navigation.

### How Agents Use It

The typical agent workflow with HCE:

1. **Index** — `hce index <source-root> --skip-summaries`
2. **Stats** — `hce stats --cache-dir ...` to gauge scale
3. **Lookup** — `hce lookup ClassName --calls` to understand structure
4. **Search** — `hce search "concept"` to discover where things live
5. **Read** — only the files that queries point to

This replaces the usual grep-read-grep-read cycle with targeted, structure-aware
navigation. For a Django-scale codebase, this means reading 5 files instead of
50 to understand a feature.

## Dependencies

Core (always installed):
- `anthropic` — for summaries
- `python-dotenv` — env loading
- `markitdown` — file conversion

Optional extras:
- `[embed]` — `sentence-transformers`, `numpy` (for Tier 4)
- `[server]` — `mcp` (for MCP server mode)
- `[text]` — `pydantic`, `instructor` (for TEXT edge extraction)
- `[all]` — everything
