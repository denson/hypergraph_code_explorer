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

Use the HCE MCP tools to index and query codebases. HCE builds a hypergraph of symbols (functions, classes, methods) and their relationships (calls, inheritance, imports), then exposes that graph through structured queries.

## IMPORTANT: Always narrate what you are doing

Before every HCE tool call, tell the user what you're about to do and why. After each tool call, summarize what you found before moving on. This is critical for user trust and transparency.

Examples of good narration:

- "Let me check if there's an existing HCE index for this repo... No cache found, so I'll index it now."
- "I'll start with an overview to find the most structurally important symbols."
- "The overview shows `HypergraphBuilder` is the most central class. Let me trace its outgoing calls to see what it depends on."
- "Found 15 symbols matching 'auth'. The most connected one is `AuthMiddleware` — let me look at what calls it."
- "Indexing 47 Python files in the django/ source directory. This will take a moment."

Never silently chain tool calls. Each step should have a brief explanation of the intent and the finding.

## Available MCP Tools

Six tools are available via the `hce` MCP server:

- **hce_index** — Index a source directory into a hypergraph. Creates `.hce_cache/` in the source root.
- **hce_lookup** — Exact symbol lookup with structural traversal (calls, callers, inherits, imports).
- **hce_search** — Text search across all symbol names.
- **hce_query** — Natural language query routed through multiple retrieval tiers.
- **hce_overview** — Codebase overview: top symbols by structural centrality.
- **hce_stats** — Graph statistics: node/edge counts, type breakdown, hub nodes.

## Workflow

### Step 1: Index (if needed)

Check whether a `.hce_cache/` directory already exists in the source root. If it does, tell the user: "Found an existing HCE index — I'll use that." Then skip to Step 2.

If no cache exists, tell the user you're going to index the codebase, then run:

```
hce_index(path="<source-root>", skip_summaries=True)
```

After indexing, report the results to the user: how many files were indexed, how many symbols and relationships were found.

**Finding the source root** — point at the directory containing the actual source code, not the repo root:

- Python: the package directory (e.g., `django/django/`, `requests/requests/`)
- Node.js: `src/` or wherever `package.json` points
- Go: the directory with `go.mod`
- Rust: `src/`
- Java: `src/main/java/`

Check `pyproject.toml`, `package.json`, `go.mod`, or `Cargo.toml` if unsure.

Always use `skip_summaries=True` (the default) — this keeps indexing zero-cost with no API key needed.

### Step 2: Explore

Start broad, then narrow. Explain your strategy to the user as you go.

1. **Get the big picture** — "Let me get an overview of the codebase structure." → `hce_overview(top=20)`
2. **Search for subsystems** — "Searching for symbols related to [topic]..." → `hce_search(term="auth")`
3. **Trace relationships** — "Tracing what [symbol] calls..." → `hce_lookup(symbol="ClassName", calls=True, depth=2)`
4. **Ask questions** — "Querying the graph for [question]..." → `hce_query(query="how does request validation work")`
5. **Check stats** — "Checking graph statistics..." → `hce_stats()`

After each tool call, summarize the key findings before deciding the next step.

### Step 3: Read source only when needed

The graph gives you structural answers (what calls what, what inherits from what) without reading files. Only read source code when you need to understand *why* something exists or *what the logic does* — and even then, read only the specific function, not the whole file. Tell the user: "The graph shows the structure, but I need to read the source to understand the logic in [function]."

## Multiple Codebases

The server keeps a registry of all indexed repos. You can work with multiple codebases in a single session:

1. Index repo A: `hce_index(path="/path/to/repo-a/src")`
2. Query repo A (it's now the active repo): `hce_lookup(symbol="Session")`
3. Index repo B: `hce_index(path="/path/to/repo-b/src")`
4. Query repo B (now active): `hce_lookup(symbol="Router")`
5. Switch back to repo A: `hce_index(path="/path/to/repo-a/src")` — loads from cache instantly
6. `hce_stats()` shows which repos are loaded and which is active

The most recently indexed or loaded repo is the "active" one that all query tools operate on. Calling `hce_index` on a repo that already has a `.hce_cache` loads it from cache without re-indexing.

Tell the user which repo is active when switching between them: "Switching to the requests codebase" or "Now querying django."

## Cache Reuse

If a repo already has a `.hce_cache/` directory (from a previous indexing session), `hce_index` will load from cache instead of re-indexing. This is fast and avoids redundant work. Tell the user: "Found an existing HCE index — loading from cache."

If the user explicitly asks to re-index (e.g., after code changes), delete the `.hce_cache/` directory first, then call `hce_index` again.

## Supported Languages

Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, PHP. Mixed-language projects are fully supported.

## Tips

- `hce_lookup` with `callers=True` answers "what calls this?" — useful for understanding impact.
- `hce_lookup` with `calls=True, depth=2` traces two levels of call chains.
- `hce_overview` ranks by importance: `2 * (calls_degree + inherits_degree) + total_degree`.
- For large codebases (1000+ nodes), use `hce_search` to narrow before `hce_lookup`.
- The graph is static analysis — dynamic dispatch and monkey-patching won't appear.

## Reference

See `references/query-guide.md` for detailed query patterns and examples.
