---
name: hce-index
description: >
  Index a codebase into a hypergraph for structural code intelligence. Use this
  skill whenever you begin working on a codebase for the first time, when a user
  asks you to explore or understand a project's architecture, or when you need to
  navigate a codebase with more than ~20 files. Triggers include: "explore this
  codebase", "help me understand this project", "index this repo", "what does
  this codebase do", "how is this project structured", or any situation where
  you're about to start reading code in a project you haven't indexed yet. Also
  trigger when: the user opens a new project or repo, you find yourself doing
  repeated grep/find to locate symbols, or you need to trace call chains or
  inheritance across files. Even if the user doesn't mention indexing, consider
  using this skill proactively when the task involves understanding unfamiliar
  code at scale.
---

# HCE: Hypergraph Code Explorer — Indexing Skill

## What This Does

HCE indexes a Python codebase into a hypergraph — a graph where edges can
connect more than two nodes. The index captures structural relationships
(imports, calls, inheritance, definitions, decorators, signatures, exceptions)
using Python AST parsing. No LLM tokens are spent; the entire pipeline is
deterministic.

After indexing, you can query the graph to understand a codebase's architecture
without reading every file. This is the difference between "grep and hope" and
"ask a structured question and get a precise answer."

## When to Index

Index a codebase when **any** of these are true:

- You're starting work on a project you haven't seen before
- The project has more than ~20 Python files
- You need to understand class hierarchies, call chains, or module dependencies
- The user asks about architecture, structure, or "how does X work" in a codebase
- You find yourself doing multiple rounds of grep/find to locate related symbols

Don't bother indexing if:
- The project has fewer than 5 Python files (just read them)
- You only need to edit a single known file
- The codebase isn't Python (HCE currently only parses Python AST)

## Step 1: Check for HCE

```bash
which hce
```

If `hce` isn't on PATH, tell the user:

> HCE isn't installed yet. You can install it with:
> ```
> pip install -e /path/to/hypergraph_code_explorer
> ```
> Or if it's published: `pip install hypergraph-code-explorer`

Don't proceed until `hce` is available.

## Step 2: Index the Codebase

Run the indexer on the project's source root. The source root is the directory
containing the actual Python packages — not the repo root (which often has
setup files, docs, etc. you don't need in the graph).

```bash
hce index <source-root> --skip-summaries
```

**Flags:**
- `--skip-summaries` — Always use this. It skips the only LLM-dependent
  feature (node summarization), keeping the pipeline zero-cost and fast.
- The index is saved to `<source-root>/.hce_cache/` automatically.

**Finding the source root:** Look for the directory that contains `__init__.py`
or the main package directory. Examples:
- `django/django/` (not `django/`)
- `fastapi/fastapi/` (not `fastapi/`)
- `requests/src/requests/` (not `requests/`)
- `my-project/src/my_package/` (not `my-project/`)

If you're unsure, check for `pyproject.toml` or `setup.py` at the repo root —
they usually point to the package location.

## Step 3: Read the Stats

```bash
hce stats --cache-dir <source-root>/.hce_cache
```

This tells you whether the hypergraph is worth using. Here's how to interpret
the numbers:

| Metric | Small (<50 files) | Medium (50-200) | Large (200+) |
|--------|-------------------|-----------------|--------------|
| Nodes | <1,500 | 1,500-5,000 | 5,000+ |
| Edges | <1,000 | 1,000-5,000 | 5,000+ |
| Hub nodes | <15 | 15-50 | 50+ |

**Decision rule:**
- **<500 nodes**: The codebase is small enough to navigate by reading files
  directly. The hypergraph exists but you probably won't need it. Mention it to
  the user as available but don't push it.
- **500-2,000 nodes**: The hypergraph is useful for targeted lookups and
  understanding structure. Use it when you need to trace relationships or find
  where symbols are defined/used.
- **>2,000 nodes**: The hypergraph is essential. Without it you'll waste
  significant context window reading files that turn out to be irrelevant. Lead
  with `hce lookup` and `hce search` before reading any source.

Report the stats to the user along with your assessment:

> Indexed [project] — [X] files, [Y] nodes, [Z] edges, [N] hub nodes.
> [Assessment: "Small project, I can navigate this directly" / "Good size for
> structural queries" / "Large codebase — I'll use the hypergraph to navigate
> efficiently"]

## Step 4: Use the Graph

Once indexed, you have four query commands. Use them **before** reading source
files — they tell you *which* files to read.

### `hce lookup <symbol>`
Exact name lookup. Use when you know (or suspect) a symbol name.

```bash
hce lookup QuerySet --cache-dir <path>/.hce_cache
```

Returns: files containing the symbol, related symbols (imports, inheritance,
definitions), and grep suggestions for finding usages.

**With `--calls`:** Shows what functions/methods the symbol calls, and what
calls it. Essential for understanding control flow.

```bash
hce lookup FastAPI --calls --cache-dir <path>/.hce_cache
```

For class nodes, this automatically expands through methods to find call edges
(since CALLS edges live on methods, not the class itself).

### `hce search "<terms>"`
Text/substring search across all node names and relations. Use for discovery
when you don't know exact symbol names.

```bash
hce search "middleware" --cache-dir <path>/.hce_cache
```

Returns file suggestions with directory collapse (3+ files from the same
directory are grouped into a single entry), matched symbols, and grep patterns.

### `hce query "<natural language>"`
Natural language query. The system tokenizes your question, filters stopwords,
and runs a multi-tier retrieval:

1. Exact name lookup in the inverted index
2. Relationship-typed BFS/DFS traversal
3. Substring text search
4. (Optional) Embedding similarity

```bash
hce query "how does request validation work" --cache-dir <path>/.hce_cache
```

Good for exploratory questions. Less precise than `lookup` but broader.

### `hce stats`
Already covered above. Also useful mid-session to remind yourself of the
graph's scale.

## Workflow Pattern

Here's the pattern that works well for navigating an unfamiliar codebase:

1. **Index** the source root
2. **Stats** to gauge scale
3. **Lookup** the entry point class or main function to understand the top-level
   structure
4. **Lookup --calls** on key classes to trace the call graph
5. **Search** for domain concepts to find where they live
6. **Read** only the files that the above queries point you to

This replaces the usual "list files → grep → read → grep more → read more"
cycle with targeted, structure-aware navigation.

## Cache Location

The index is saved to `.hce_cache/` inside the source root. If you're running
queries from a different working directory, use `--cache-dir` to point at it:

```bash
hce lookup MyClass --cache-dir /path/to/project/src/package/.hce_cache
```

The cache persists across sessions. You only need to re-index if the codebase
has changed significantly (new modules, major refactors). For small changes,
the existing index is still useful.

## Limitations

- **Python only.** HCE parses Python AST. Other languages aren't supported yet.
- **Static analysis.** Dynamic dispatch, monkey-patching, and runtime
  metaprogramming aren't captured. If a codebase heavily uses `getattr()` or
  dynamic imports, some edges will be missing.
- **No semantic understanding.** The graph captures structure (what calls what,
  what inherits from what) but not intent. You still need to read the actual
  code to understand *why* something works the way it does.
- **Hub node filtering.** Very common symbols (like `len`, `isinstance`,
  `property`) are filtered as hub nodes to prevent traversal explosion. This
  means `lookup len` won't show you everything that calls `len` — which is
  intentional, since that would be nearly every file.
