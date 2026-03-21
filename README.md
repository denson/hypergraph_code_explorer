# Hypergraph Code Explorer (HCE)

Structural code intelligence for AI agents. HCE indexes a Python codebase into a hypergraph — a graph where edges connect multiple nodes — and provides instant lookups of call chains, inheritance trees, import graphs, and symbol relationships. Zero LLM tokens. Deterministic. Sub-second queries on codebases with 20,000+ nodes.

## Why

AI coding agents (Claude Code, Cursor, Codex, Cowork) spend most of their context window figuring out *which files to read*. The usual approach — grep, list files, read one, grep again — burns tokens and context on dead ends. HCE replaces that fumbling with structural queries: "what does this class call?", "who inherits from this?", "where does this symbol live?" — answered in milliseconds from a prebuilt index.

## Quick Start

```bash
# Install
pip install -e .

# Index a codebase (the source root, not the repo root)
hce index ./my-project/src/my_package --skip-summaries

# Look up a symbol
hce lookup MyClass

# See what it calls
hce lookup MyClass --calls

# Search by concept
hce search "authentication"

# Natural language query
hce query "how does request validation work"

# Check the graph stats
hce stats --cache-dir ./my-project/src/my_package/.hce_cache
```

The `--skip-summaries` flag keeps the entire pipeline zero-cost (no API calls). The index is saved to `.hce_cache/` inside the source root and persists across sessions.

## What It Indexes

HCE parses Python AST to extract seven types of structural relationships:

| Edge Type | What It Captures | Example |
|-----------|-----------------|---------|
| CALLS | Function/method call sites | `Session.send` → `HTTPAdapter.send` |
| IMPORTS | Import statements | `routing` → `starlette.routing` |
| DEFINES | Class/function definitions | `FastAPI` → `FastAPI.__init__`, `FastAPI.get` |
| INHERITS | Class inheritance | `FastAPI` → `Starlette` |
| SIGNATURE | Parameter types | `Depends(dependency, use_cache)` |
| RAISES | Exceptions raised | `validate()` → `ValidationError` |
| DECORATES | Decorator usage | `@dataclass` → `Depends` |

Each edge is a **hyperedge** — it can connect more than two nodes. This means a single CALLS edge captures caller, callee, and all arguments, giving richer context than pairwise edges.

## Commands

### `hce index <path>`

Index a Python source directory. Points at the package root (the directory with `__init__.py`), not the repo root.

```bash
hce index ./django/django --skip-summaries --verbose
```

**Finding the source root:** Look at `pyproject.toml` or `setup.py` — they point to the package location. Common patterns: `django/django/`, `fastapi/fastapi/`, `requests/src/requests/`.

### `hce lookup <symbol>`

Exact symbol lookup. Returns files containing the symbol, related symbols (inheritance, imports, definitions), and grep suggestions.

```bash
hce lookup QuerySet                    # find the class
hce lookup QuerySet --calls            # what does it call?
hce lookup HttpResponse --cache-dir .  # specify cache location
```

For class nodes, `--calls` automatically expands through methods to find their call edges.

### `hce search "<terms>"`

Text/substring search across all node names. Good for discovery when you don't know exact symbol names.

```bash
hce search "middleware"
hce search "dependency injection"
```

Directories with 3+ matching files are collapsed into a single entry to reduce noise.

### `hce query "<question>"`

Natural language query. Tokenizes the question, filters stopwords, and runs multi-tier retrieval (exact lookup → structural traversal → text search).

```bash
hce query "how does the ORM build SQL queries"
hce query "what middleware handles authentication"
```

### `hce stats`

Graph statistics: node count, edge count, edge types, hub nodes.

```bash
hce stats --cache-dir ./django/django/.hce_cache
```

### `hce overview`

High-level codebase map: key symbols, call chains, inheritance trees.

## Scale

Tested on real codebases:

| Codebase | Files | Nodes | Edges | Hub Nodes | Index Time |
|----------|-------|-------|-------|-----------|------------|
| requests | 18 | 906 | 485 | 11 | ~3s |
| FastAPI | 48 | 1,264 | 1,214 | 13 | ~9s |
| Django | 1,163 | 23,614 | 19,382 | 103 | ~196s |

Hub node filtering uses a hybrid threshold (`min(3% of edges, floor of 50)`) to keep traversals clean at any scale.

## Agent Skill

The `skills/hce-index/` directory contains a ready-to-use skill that teaches AI agents to automatically index new codebases and use the graph for navigation.

**Install for Claude Code (global):**
```bash
cp -r skills/hce-index ~/.claude/skills/
```

**Install for Claude Code (per-project):**
```bash
cp -r skills/hce-index .claude/skills/
```

The skill handles the full workflow: detect that a codebase needs indexing, run the index, read the stats, decide if the graph is worth using, and switch to structural queries for navigation.

## Optional Features

**Embeddings (Tier 4 semantic search):**
```bash
pip install -e ".[embed]"
hce embed --cache-dir .hce_cache
```

**MCP Server:**
```bash
pip install -e ".[server]"
hce server
```

Exposes `hce_lookup`, `hce_search`, `hce_query`, `hce_overview`, and `hce_stats` as MCP tools.

**LLM Summaries:**
```bash
# Requires ANTHROPIC_API_KEY in .env
hce index ./my-project/src/my_package   # without --skip-summaries
```

Generates file-level summaries stored as SUMMARY edges. Useful but costs API tokens.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for internals: data structures, tiered retrieval system, edge types, module dependency order, and MCP tool schemas.

## Development

```bash
# Install with dev dependencies
pip install -e ".[all]"
pip install pytest

# Run tests (148 passing)
pytest

# Index the test codebase
hce index ../requests/src/requests --skip-summaries
```

## Limitations

- **Python only.** HCE parses Python AST. Other languages aren't supported yet.
- **Static analysis.** Dynamic dispatch, monkey-patching, and `getattr()` magic aren't captured.
- **Structure, not semantics.** The graph tells you *what calls what* but not *why*. You still need to read the code for business logic.
