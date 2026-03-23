---
name: hce-explore
description: >
  Explore and understand any codebase using HCE (Hypergraph Code Explorer).
  Use when the user asks to "explore this codebase", "understand this repo",
  "map the architecture", "what does this code do", "how does X work in the code",
  "index this repo with HCE", "use HCE", or any request to navigate, query,
  or understand source code structure. Also triggers on mentions of "hypergraph",
  "code graph", "HCE", "symbol lookup", or "codebase analysis".
---

# HCE Explore

HCE builds a hypergraph of symbols (functions, classes, methods) and their relationships (calls, inheritance, imports), then makes that graph queryable in milliseconds.

## IMPORTANT: Always narrate what you are doing

Before every HCE operation, tell the user what you're about to do and why. After each operation, summarize what you found before moving on. This is critical for user trust and transparency.

Examples of good narration:

- "Let me check if there's an existing HCE index for this repo... No cache found, so I'll index it now."
- "I'll start with an overview to find the most structurally important symbols."
- "The overview shows `HypergraphBuilder` is the most central class. Let me trace its outgoing calls to see what it depends on."
- "Found 15 symbols matching 'auth'. The most connected one is `AuthMiddleware` — let me look at what calls it."
- "Indexing 47 Python files in the django/ source directory. This will take a moment."

Never silently chain operations. Each step should have a brief explanation of the intent and the finding.

## Choosing the right interface

HCE can be used two ways. Choose based on what's available:

### Option 1: MCP tools (Cowork)

If MCP tools named `hce_index`, `hce_lookup`, `hce_search`, `hce_query`, `hce_overview`, `hce_stats` are available in your tool set, use them directly. This is the typical path in Cowork sessions. See the "MCP Tools Reference" section below.

### Option 2: Python API (Claude Code / terminal)

If MCP tools are NOT available, use the Python API via Bash. This is the typical path in Claude Code.

**Step 0: Ensure HCE is installed**

```bash
python -c "import hypergraph_code_explorer" 2>/dev/null || pip install "hypergraph-code-explorer @ git+https://github.com/denson/hypergraph_code_explorer.git" --break-system-packages --quiet
```

**Indexing:**

```python
python -c "
from hypergraph_code_explorer.pipeline import HypergraphPipeline
from hypergraph_code_explorer.codemap import generate_codemap
p = HypergraphPipeline(verbose=True, skip_summaries=True)
stats = p.index_directory('<source-root>')
generate_codemap(p.builder, cache_dir=p._cache_dir)
print(stats)
"
```

**Loading and querying:**

```python
python -c "
from hypergraph_code_explorer.api import HypergraphSession
import json
s = HypergraphSession.load('<source-root>/.hce_cache')
print(json.dumps(s.stats(), indent=2))
"
```

**Available API methods on HypergraphSession:**

- `s.stats()` — node/edge counts, type breakdown, hub nodes
- `s.overview(top=20)` — modules and top symbols by centrality
- `s.search('term', max_results=20)` — text search across symbol names
- `s.lookup('SymbolName', edge_types=['CALLS'], depth=2, direction='forward')` — structural traversal
- `s.query('natural language question', depth=2)` — multi-tier retrieval

**Direction values for lookup:** `'forward'` (calls), `'backward'` (callers), `'both'`

**Edge types:** `'CALLS'`, `'IMPORTS'`, `'INHERITS'`, `'DEFINES'`, `'RAISES'`, `'SIGNATURE'`, `'DECORATES'`

**Formatting results:** Use `from hypergraph_code_explorer.retrieval.plan import format_json` to get readable output from `lookup`, `search`, and `query` results.

## Workflow

### Step 1: Find the source root

Point at the directory containing the actual source code, not the repo root:

- Python: the package directory (e.g., `django/django/`, `requests/src/requests/`)
- Node.js: `src/` or wherever `package.json` points
- Go: the directory with `go.mod`
- Rust: `src/`
- Java: `src/main/java/`

Check `pyproject.toml`, `package.json`, `go.mod`, or `Cargo.toml` if unsure.

### Step 2: Index (if needed)

Check whether a `.hce_cache/` directory already exists in the source root. If it does, tell the user: "Found an existing HCE index — I'll use that." Load from cache instead of re-indexing.

If no cache exists, tell the user you're going to index the codebase, then index it. After indexing, report the results: how many files were indexed, how many symbols and relationships were found.

### Step 3: Explore

Start broad, then narrow. Explain your strategy to the user as you go.

1. **Get the big picture** — `overview(top=20)` shows the most structurally central symbols.
2. **Search for subsystems** — `search('auth')` finds symbols by name substring.
3. **Trace relationships** — `lookup('ClassName', edge_types=['CALLS'], depth=2, direction='forward')` shows what a symbol calls.
4. **Ask questions** — `query('how does request validation work')` for natural language.
5. **Check stats** — `stats()` for graph size and composition.

After each operation, summarize the key findings before deciding the next step.

### Step 4: Read source only when needed

The graph gives you structural answers (what calls what, what inherits from what) without reading files. Only read source code when you need to understand *why* something exists or *what the logic does* — and even then, read only the specific function, not the whole file.

## Multiple Codebases

You can work with multiple codebases in a single session by maintaining separate sessions:

**MCP mode:** Call `hce_index` on each repo. The server maintains a registry — the most recently loaded repo is "active." Calling `hce_index` on a repo with an existing cache loads instantly.

**Python API mode:** Create separate `HypergraphSession` objects for each repo and query them independently.

Tell the user which repo is active when switching between them.

## Cache Reuse

If a repo already has a `.hce_cache/` directory, load from cache instead of re-indexing. This is fast and avoids redundant work. Tell the user: "Found an existing HCE index — loading from cache."

If the user explicitly asks to re-index (e.g., after code changes), delete the `.hce_cache/` directory first, then index again.

## MCP Tools Reference

When MCP tools are available, use these:

- **hce_index(path, skip_summaries=True)** — Index a source directory or load from existing cache.
- **hce_lookup(symbol, calls, callers, inherits, imports, depth)** — Exact symbol lookup with structural traversal.
- **hce_search(term, max_results)** — Text search across all symbol names.
- **hce_query(query, depth)** — Natural language query through multiple retrieval tiers.
- **hce_overview(top)** — Codebase overview: top symbols by structural centrality.
- **hce_stats()** — Graph statistics and list of loaded repos.

## Supported Languages

Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, PHP. Mixed-language projects are fully supported.

## Tips

- `lookup` with `direction='backward'` answers "what calls this?" — useful for understanding impact.
- `lookup` with `direction='forward', depth=2` traces two levels of call chains.
- `overview` ranks by importance: `2 * (calls_degree + inherits_degree) + total_degree`.
- For large codebases (1000+ nodes), use `search` to narrow before `lookup`.
- The graph is static analysis — dynamic dispatch and monkey-patching won't appear.

## Reference

See `references/query-guide.md` for detailed query patterns and examples.
