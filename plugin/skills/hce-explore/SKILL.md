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

Before every HCE operation, tell the user what you're about to do and why. After each operation, summarize what you found before moving on.

Examples of good narration:

- "Let me check if there's an existing HCE index for this repo... No cache found, so I'll index it now."
- "I'll start with an overview to find the most structurally important symbols."
- "The overview shows `HypergraphBuilder` is the most central class. Let me trace its outgoing calls."
- "Indexing 47 Python files in the django/ source directory. This will take a moment."

Never silently chain operations. Each step should have a brief explanation of the intent and the finding.

## How to use HCE

Use the **Python API** via Bash commands. Do NOT try to use the `hce` CLI command — it has PATH issues on Windows. Do NOT try `python -m hypergraph_code_explorer` — it has no `__main__`. Always use `python -c "..."` with the API directly.

If HCE MCP tools (`hce_index`, `hce_lookup`, etc.) are available in your tool set, you may use those instead. But the Python API is the primary and most reliable interface.

### Ensure HCE is installed

Run this first. If already installed it returns instantly:

```bash
python -c "import hypergraph_code_explorer" 2>/dev/null || pip install "hypergraph-code-explorer @ git+https://github.com/denson/hypergraph_code_explorer.git" --break-system-packages --quiet
```

### Check for existing cache

Before indexing, always check if `.hce_cache/` already exists in the source root:

```bash
ls <source-root>/.hce_cache/manifest.json 2>/dev/null && echo "CACHE EXISTS" || echo "NO CACHE"
```

If the cache exists, skip indexing and go straight to loading and querying.

### Index a codebase (only if no cache)

```bash
python -c "
from hypergraph_code_explorer.pipeline import HypergraphPipeline
from hypergraph_code_explorer.codemap import generate_codemap
p = HypergraphPipeline(verbose=True, skip_summaries=True)
stats = p.index_directory('<source-root>')
generate_codemap(p.builder, cache_dir=p._cache_dir)
import json; print(json.dumps(stats, indent=2))
"
```

Replace `<source-root>` with the actual path. This creates `.hce_cache/` in the source root.

### Load and query

All queries go through `HypergraphSession`:

```bash
python -c "
from hypergraph_code_explorer.api import HypergraphSession
from hypergraph_code_explorer.retrieval.plan import format_json
import json
s = HypergraphSession.load('<source-root>/.hce_cache')

# Pick ONE of these per call:
print(json.dumps(s.stats(), indent=2))
# print(json.dumps(s.overview(top=20), indent=2))
# print(format_json(s.search('term', max_results=20)))
# print(format_json(s.lookup('SymbolName', edge_types=['CALLS'], depth=2, direction='forward')))
# print(format_json(s.query('natural language question', depth=2)))
"
```

### API reference

**`s.stats()`** — Returns dict with `num_nodes`, `num_edges`, `edge_type_counts`, `hub_nodes`.

**`s.overview(top=20)`** — Returns dict with `modules` (file paths) and `key_symbols` (ranked by centrality).

**`s.search('term', max_results=20)`** — Text search across symbol names. Returns a retrieval plan — use `format_json()` to render.

**`s.lookup('Symbol', edge_types=None, depth=1, direction='both')`** — Structural traversal from a symbol. Returns a retrieval plan.
- `edge_types`: list of `'CALLS'`, `'IMPORTS'`, `'INHERITS'`, `'DEFINES'`, `'RAISES'`, `'SIGNATURE'`, `'DECORATES'`
- `direction`: `'forward'` (what it calls), `'backward'` (what calls it), `'both'`
- `depth`: how many levels to traverse (1-3 recommended)

**`s.query('question', depth=2)`** — Natural language query through multiple retrieval tiers. Returns a retrieval plan.

**`format_json(plan)`** — Converts a retrieval plan to readable JSON string. Import from `hypergraph_code_explorer.retrieval.plan`.

### Memory tours

Memory tours let you persist useful graph query results as reusable architectural notes. For a deeper guide on using tours as structured agent memory (parallel views, multi-agent annotations, composition patterns), see `docs/memory-tours-guide.md`. They access the full graph (all edge types) and carry provenance metadata. Tours are ephemeral by default; promote the useful ones to durable memory.

**`s.memory_tour_create(plan, name='...', tags=['...'])`** — Scaffold a memory tour from a retrieval plan and persist it. Returns a dict with id, steps, keywords, etc.

**`s.memory_tour_list(tag='...', promoted_only=False)`** — List all memory tours as dicts. Filter by tag or promotion status.

**`s.memory_tour_get(tour_id)`** — Retrieve a single tour by ID. Records usage automatically.

**`s.memory_tour_promote(tour_id)`** — Mark a tour as promoted (durable memory).

**`s.memory_tour_remove(tour_id)`** — Delete a tour.

**`s.memory_tour_scaffold_prompt(plan)`** — Generate a structured prompt for LLM-authored tour creation.

**`s.memory_tour_create_from_dict(data)`** — Create a tour from raw JSON (e.g. LLM-authored output).

**Memory tour workflow example:**

```bash
python -c "
from hypergraph_code_explorer.api import HypergraphSession
import json
s = HypergraphSession.load('<source-root>/.hce_cache')

# Create a memory tour from a query
plan = s.query('how does authentication work', depth=2)
tour = s.memory_tour_create(plan, name='Auth Flow', tags=['auth'])
print(json.dumps(tour, indent=2))

# Later: list and recall tours
tours = s.memory_tour_list(tag='auth')
print(json.dumps(tours, indent=2))

# Promote useful tours to durable memory
s.memory_tour_promote(tour['id'])
"
```

## Workflow

### Step 1: Find the source root

Point at the directory containing the actual source code, not the repo root:

- Python: the package directory (e.g., `django/django/`, `requests/src/requests/`)
- Node.js: `src/` or wherever `package.json` points
- Go: the directory with `go.mod`
- Rust: `src/`
- Java: `src/main/java/`

Check `pyproject.toml`, `package.json`, `go.mod`, or `Cargo.toml` if unsure.

### Step 2: Index or load cache

Check for `.hce_cache/`. If found, tell the user "Found an existing HCE index — loading from cache." If not, tell the user you're indexing and run the index command. Report results: files indexed, symbols, relationships.

### Step 3: Explore

Start broad, then narrow:

1. **Stats** — Get the scale: `s.stats()`
2. **Overview** — Find the important symbols: `s.overview(top=20)`
3. **Search** — Find symbols by name: `s.search('auth')`
4. **Lookup** — Trace relationships: `s.lookup('Session', edge_types=['CALLS'], depth=2, direction='forward')`
5. **Query** — Ask questions: `s.query('how does request validation work')`

After each operation, summarize findings before deciding the next step.

### Step 4: Read source only when needed

The graph gives structural answers without reading files. Only read source when you need to understand *why* something exists or *what the logic does* — and even then, read only the specific function.

## Multiple Codebases

Load separate `HypergraphSession` objects for each repo:

```python
s_requests = HypergraphSession.load('/path/to/requests/src/requests/.hce_cache')
s_django = HypergraphSession.load('/path/to/django/django/.hce_cache')
```

## Supported Languages

Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, PHP. Mixed-language projects are fully supported.

## Tips

- `lookup` with `direction='backward'` answers "what calls this?"
- `lookup` with `direction='forward', depth=2` traces two levels of call chains
- `overview` ranks by: `2 * (calls_degree + inherits_degree) + total_degree`
- For large codebases (1000+ nodes), use `search` to narrow before `lookup`
- The graph is static analysis — dynamic dispatch and monkey-patching won't appear

## Reference

See `references/query-guide.md` for detailed query patterns and examples.
