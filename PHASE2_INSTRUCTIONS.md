# Phase 2 Instructions for Claude Code

Read PLAN_V3.md first for full context. This document covers:
1. A bug fix in dispatch.py from Phase 1
2. Phase 2 implementation: CLI rewrite, CODEBASE_MAP generator, init command
3. Phase 3 implementation: pipeline/api/mcp updates, dependency cleanup

## Bug Fix: dispatch.py text→Tier 1/2 feedback never triggers

In `retrieval/dispatch.py`, lines 142-164, there is a logic error:

```python
# --- Tier 3: Text search (if Tier 1 didn't find enough) ---
if "text_search" in classifications or plan.is_empty():
    t3_plan = text_search(query, builder, max_results=max_results)
    plan.merge(t3_plan)

    # If text search found nodes that Tier 1 missed, try Tier 1+2 on them
    if t3_plan.related_symbols and plan.is_empty():   # <-- BUG HERE
```

After `plan.merge(t3_plan)`, `plan` now contains Tier 3 results, so `plan.is_empty()` is always False. The text→Tier 1/2 feedback path (lines 148-164) never executes.

**Fix:** Check whether Tier 1 was empty (i.e., no identifier classification), not whether the merged plan is empty. The intent is: "if we got here via text search (not via Tier 1), expand the text matches structurally."

Replace lines 141-164 with:

```python
    # --- Tier 3: Text search (if Tier 1 didn't find enough) ---
    if "text_search" in classifications or plan.is_empty():
        t3_plan = text_search(query, builder, max_results=max_results)
        plan.merge(t3_plan)

        # If we reached Tier 3 without Tier 1 results, expand text matches structurally
        if t3_plan.related_symbols and "identifier" not in classifications:
            # Feed text search results into Tier 1 for structural expansion
            text_nodes = [s.name for s in t3_plan.related_symbols[:5]]
            for node in text_nodes:
                t1_sub = lookup(node, builder, edge_types=edge_types)
                plan.merge(t1_sub)

            # And Tier 2 traversal
            if text_nodes:
                t2_sub = traverse(
                    text_nodes[:3],
                    builder,
                    edge_types=edge_types,
                    depth=min(depth, 1),  # shallow for text-search seeds
                    direction=direction,
                    hub_nodes=hub_nodes,
                )
                plan.merge(t2_sub)
```

Add a test for this in `tests/test_dispatch.py`:

```python
def test_text_search_feeds_into_structural_expansion():
    """When Tier 1 finds nothing but Tier 3 finds substring matches,
    those matches should be expanded structurally via Tier 1+2."""
    builder = HypergraphBuilder()
    # Create nodes that won't match "authentication" exactly
    # but will match via substring ("auth")
    builder.add_edge(HyperedgeRecord(
        edge_id="e1", relation="auth module defines AuthBase",
        edge_type="DEFINES", sources=["auth"], targets=["auth.AuthBase"],
        source_path="auth.py",
    ))
    builder.add_edge(HyperedgeRecord(
        edge_id="e2", relation="AuthBase calls check_credentials",
        edge_type="CALLS", sources=["auth.AuthBase"], targets=["check_credentials"],
        source_path="auth.py",
    ))

    plan = dispatch("how does authentication work", builder)

    # Should have found auth-related nodes via text search
    # AND expanded them structurally
    assert len(plan.primary_files) > 0
    assert any("auth" in f.path.lower() for f in plan.primary_files)
    # Should have tier 3 (text search) in tiers_used
    assert 3 in plan.tiers_used
```

---

## Phase 2: CLI, CODEBASE_MAP, init command

### 2A. Rewrite `cli.py`

Replace the current `cli.py` with new subcommands. Keep the `index` command mostly unchanged (but add codemap generation at the end). Add: `lookup`, `search`, `query`, `overview`, `init`, `embed`, `stats`.

Every command that produces output should support `--json` for structured output. Default is human-readable text via `format_text()` from `retrieval/plan.py`.

The `--cache-dir` pattern from the old `query` command should apply to all read commands (`lookup`, `search`, `query`, `overview`, `stats`). They all need to load the builder from `.hce/`.

```python
"""
CLI Interface
=============
Subcommands: index, lookup, search, query, overview, init, embed, stats, server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="hce",
        description="Hypergraph Code Explorer — structural code intelligence",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- index ----
    index_p = subparsers.add_parser("index", help="Index a codebase")
    index_p.add_argument("path", type=str, help="Path to directory to index")
    index_p.add_argument("--text-edges", action="store_true",
                         help="Enable LLM-based text edge extraction")
    index_p.add_argument("--skip-summaries", action="store_true",
                         help="Skip summary generation")
    index_p.add_argument("--embed", action="store_true",
                         help="Compute embeddings at index time (for Tier 4)")
    index_p.add_argument("--summary-model", choices=["haiku", "sonnet"],
                         default="haiku", help="Model for summaries")
    index_p.add_argument("--verbose", "-v", action="store_true")

    # ---- lookup ----
    lookup_p = subparsers.add_parser("lookup", help="Look up a symbol in the graph")
    lookup_p.add_argument("symbol", type=str, help="Symbol name to look up")
    lookup_p.add_argument("--calls", action="store_true", help="Show what it calls")
    lookup_p.add_argument("--callers", action="store_true", help="Show what calls it")
    lookup_p.add_argument("--inherits", action="store_true", help="Show inheritance")
    lookup_p.add_argument("--imports", action="store_true", help="Show imports")
    lookup_p.add_argument("--raises", action="store_true", help="Show exceptions raised")
    lookup_p.add_argument("--depth", type=int, default=1, help="Traversal depth (default: 1)")
    lookup_p.add_argument("--json", action="store_true", dest="json_output",
                          help="Output as JSON")
    lookup_p.add_argument("--cache-dir", type=str, default=None)
    lookup_p.add_argument("--verbose", "-v", action="store_true")

    # ---- search ----
    search_p = subparsers.add_parser("search", help="Text search across symbols")
    search_p.add_argument("term", type=str, help="Search term")
    search_p.add_argument("--type", type=str, default=None,
                          help="Filter by edge type (CALLS, IMPORTS, etc.)")
    search_p.add_argument("--json", action="store_true", dest="json_output")
    search_p.add_argument("--cache-dir", type=str, default=None)
    search_p.add_argument("--verbose", "-v", action="store_true")

    # ---- query ----
    query_p = subparsers.add_parser("query", help="Natural language query")
    query_p.add_argument("query", type=str, help="Natural language question")
    query_p.add_argument("--depth", type=int, default=2, help="Traversal depth")
    query_p.add_argument("--json", action="store_true", dest="json_output")
    query_p.add_argument("--cache-dir", type=str, default=None)
    query_p.add_argument("--verbose", "-v", action="store_true")

    # ---- overview ----
    overview_p = subparsers.add_parser("overview", help="Codebase overview")
    overview_p.add_argument("--top", type=int, default=10,
                            help="Top N symbols by degree")
    overview_p.add_argument("--json", action="store_true", dest="json_output")
    overview_p.add_argument("--cache-dir", type=str, default=None)
    overview_p.add_argument("--verbose", "-v", action="store_true")

    # ---- init ----
    init_p = subparsers.add_parser("init", help="Generate tool instruction files")
    init_p.add_argument("--tool", type=str, default="all",
                        choices=["claude-code", "cursor", "codex", "all"],
                        help="Which tool to generate instructions for")
    init_p.add_argument("--cache-dir", type=str, default=None)

    # ---- embed ----
    embed_p = subparsers.add_parser("embed", help="Compute embeddings for Tier 4")
    embed_p.add_argument("--force", action="store_true",
                         help="Recompute even if embeddings exist")
    embed_p.add_argument("--cache-dir", type=str, default=None)
    embed_p.add_argument("--verbose", "-v", action="store_true")

    # ---- stats ----
    stats_p = subparsers.add_parser("stats", help="Show graph statistics")
    stats_p.add_argument("--json", action="store_true", dest="json_output")
    stats_p.add_argument("--cache-dir", type=str, default=None)

    # ---- server ----
    subparsers.add_parser("server", help="Start MCP server")

    args = parser.parse_args()

    # Route to handler
    handlers = {
        "index": _run_index,
        "lookup": _run_lookup,
        "search": _run_search,
        "query": _run_query,
        "overview": _run_overview,
        "init": _run_init,
        "embed": _run_embed,
        "stats": _run_stats,
        "server": _run_server,
    }
    handlers[args.command](args)
```

**Handler implementations:**

`_run_index(args)` — Keep the existing indexing logic. After indexing completes and `_save_state()` runs, also call the codemap generator:

```python
    # After pipeline.index_directory() and printing stats:
    from .codemap import generate_codemap
    generate_codemap(pipeline.builder, cache_dir=pipeline._cache_dir)
```

If `--embed` is passed, run `pipeline.embeddings.embed_all_from_builder(pipeline.builder)` and save. Otherwise skip embeddings entirely (remove the existing `embed_all_from_builder` call from the default index flow in pipeline.py).

`_run_lookup(args)` — Load builder from cache dir. Determine edge types from flags:

```python
def _run_lookup(args):
    builder = _load_builder(args.cache_dir)

    # Determine edge types from flags
    edge_types = []
    if args.calls: edge_types.append("CALLS")
    if args.callers: edge_types.append("CALLS")  # same type, different direction
    if args.inherits: edge_types.append("INHERITS")
    if args.imports: edge_types.append("IMPORTS")
    if args.raises: edge_types.append("RAISES")
    if not edge_types:
        edge_types = None  # show all

    # Determine direction
    direction = "both"
    if args.calls and not args.callers:
        direction = "forward"
    elif args.callers and not args.calls:
        direction = "backward"

    from .retrieval.lookup import lookup
    from .retrieval.traverse import traverse
    from .retrieval.plan import format_text, format_json

    plan = lookup(args.symbol, builder, edge_types=edge_types)

    # If depth > 0, run Tier 2 traversal from the looked-up nodes
    if args.depth > 0 and not plan.is_empty():
        seed_nodes = list({s.name for s in plan.related_symbols})
        t2 = traverse(
            seed_nodes[:5], builder,
            edge_types=edge_types, depth=args.depth, direction=direction,
        )
        plan.merge(t2)

    if args.json_output:
        print(format_json(plan))
    else:
        print(format_text(plan))
```

`_run_search(args)` — Load builder, run `text_search()`, print result.

`_run_query(args)` — Load builder, run `dispatch()`, print result.

`_run_overview(args)` — Load builder, compute stats + degree-sorted nodes + summaries. Output overview section. Use the Overview dataclass from plan.py.

`_run_init(args)` — Write instruction files. See section 2C below.

`_run_embed(args)` — Load builder, load or create EmbeddingManager, run `embed_all_from_builder`, save.

`_run_stats(args)` — Load builder, call `builder.stats()`, add hub node count, print.

`_run_server(args)` — Unchanged.

**Helper: `_load_builder(cache_dir)`** — Shared logic for all read commands:

```python
def _load_builder(cache_dir: str | None) -> HypergraphBuilder:
    from .graph.builder import HypergraphBuilder

    if cache_dir:
        path = Path(cache_dir) / "builder.pkl"
    else:
        # Search for .hce_cache in cwd and parent dirs
        search = Path.cwd()
        for _ in range(5):
            candidate = search / ".hce_cache" / "builder.pkl"
            if candidate.exists():
                path = candidate
                break
            search = search.parent
        else:
            print("Error: No cached index found. Run 'hce index <path>' first.",
                  file=sys.stderr)
            sys.exit(1)

    return HypergraphBuilder.load(path)
```

Note: the search-up-parent-dirs logic means `hce lookup Session.send --calls` works from any subdirectory of the indexed project, like `git` does.

### 2B. CODEBASE_MAP.md Generator — `codemap.py`

New file: `src/hypergraph_code_explorer/codemap.py`

This generates `.hce/CODEBASE_MAP.md` from the builder state.

```python
"""
CODEBASE_MAP.md Generator
==========================
Generates a static structural overview of the codebase from the hypergraph.
This file is included in agent context automatically (CLAUDE.md, .cursorrules, etc.)
and gives the agent enough structural knowledge to know when to call `hce`.

Content caps (for large codebases):
  - Top 100 symbols by degree
  - Top 20 call chains by depth
  - Top 10 inheritance trees
  - No line numbers anywhere
"""
```

**Sections to generate:**

1. **Modules** — List all unique `source_path` values from edges. If SUMMARY edges exist, use their `relation` text as the one-line description. Otherwise, derive from DEFINES edges: "defines ClassName, function_name, ...".

2. **Key Symbols** — Get all nodes, sort by degree (`len(builder._node_to_edges[node])`), take top 100. Table format: `| Symbol | File | Degree |`. Get the file from the first DEFINES edge for that node.

3. **Call Chains** — Walk CALLS edges from high-degree source nodes. For each starting node, follow source→target through CALLS edges, building chains. Deduplicate, sort by chain length, take top 20.

    Algorithm:
    ```
    for each node sorted by CALLS-edge out-degree (descending):
        chain = [node]
        current = node
        while current has CALLS edges:
            targets = CALLS edge targets for current
            next = first target not already in chain
            if next is None: break
            chain.append(next)
            current = next
        if len(chain) >= 2:
            chains.append(chain)
    deduplicate chains (remove chains that are subsets of longer chains)
    sort by length descending
    take top 20
    ```

4. **Inheritance Trees** — Collect all INHERITS edges. Group by base class (target). Format: `BaseClass ← Child1, Child2, Child3`. Sort by number of children, take top 10.

5. **CLI Quick Reference** — Static text block (hardcoded).

**Output format:**

```markdown
# Code Map
<!-- Auto-generated by hce. Regenerate with: hce index <path> -->

## Modules
- path/to/file.py — One-line description from summary or defines.
...

## Key Symbols (top N by connectivity)
| Symbol | File | Degree |
|--------|------|--------|
| module.ClassName | file.py | 24 |
...

## Call Chains (top N by depth)
- entry_point → step1 → step2 → step3
...

## Inheritance Trees (top N)
- BaseClass ← Child1, Child2, Child3
...

## CLI Quick Reference
  hce lookup <symbol> --calls    # what does this symbol call?
  hce lookup <symbol> --inherits # class hierarchy
  hce search "term"              # find symbols by name
  hce query "question"           # natural language query
  # Add --json to any command for structured output
```

The function signature:

```python
def generate_codemap(
    builder: HypergraphBuilder,
    cache_dir: Path | None = None,
    max_symbols: int = 100,
    max_call_chains: int = 20,
    max_inheritance: int = 10,
) -> str:
    """Generate CODEBASE_MAP.md content and optionally save to disk.

    Args:
        builder: The populated hypergraph builder.
        cache_dir: If provided, save to cache_dir/CODEBASE_MAP.md.
        max_symbols: Cap for key symbols table.
        max_call_chains: Cap for call chains.
        max_inheritance: Cap for inheritance trees.

    Returns:
        The generated markdown string.
    """
```

### 2C. Init Command — `init.py`

New file: `src/hypergraph_code_explorer/init.py`

Generates tool-specific instruction files. If a file already exists, search for an existing `## Code Intelligence` section — if found, replace it; if not, append it.

The instruction content for each tool is defined in PLAN_V3.md under "Tool Instruction Files." Copy those templates exactly.

```python
def generate_init_file(
    tool: str,
    project_dir: Path | None = None,
) -> Path:
    """Generate a tool instruction file.

    Args:
        tool: "claude-code", "cursor", or "codex"
        project_dir: Where to write the file. Defaults to cwd.

    Returns:
        Path to the generated/updated file.
    """
```

File targets:
- `claude-code` → `CLAUDE.md` (in project root, NOT in `.hce/`)
- `cursor` → `.cursorrules` (in project root)
- `codex` → `AGENTS.md` (in project root)
- `all` → generate all three

When appending to an existing file, add a blank line separator before the `## Code Intelligence` section.

### 2D. Integration Tests

Create `tests/test_cli_integration.py` with tests that:

1. Build a small in-memory hypergraph (reuse fixtures from Phase 1 tests)
2. Save it to a temp directory as `.hce_cache/builder.pkl`
3. Call the CLI handler functions directly (not via subprocess, since we can't install deps in test)
4. Verify output contains expected file paths, symbols, grep patterns

Also create `tests/test_codemap.py`:

1. Build a hypergraph with DEFINES, CALLS, INHERITS edges
2. Call `generate_codemap(builder)`
3. Verify the markdown contains expected sections
4. Verify caps are respected (add >100 nodes, check only 100 appear)
5. Verify no line numbers appear anywhere in output

And `tests/test_init.py`:

1. Call `generate_init_file("claude-code", tmp_path)`
2. Verify CLAUDE.md exists and contains "Code Intelligence" and "--json"
3. Call again — verify it replaces the section, doesn't duplicate
4. Test with pre-existing CLAUDE.md content — verify other content preserved

---

## Phase 3: Pipeline/API/MCP cleanup + dependency pruning

### 3A. Update `pipeline.py`

- Remove `from .graph.embeddings import EmbeddingManager` from the default init. Make embeddings lazy.
- Remove `self.embeddings.embed_all_from_builder(self.builder)` from `index_directory()` unless `--embed` was passed.
- Remove `self.embeddings = EmbeddingManager(...)` from `__init__`. Instead, create it on demand when Tier 4 is invoked.
- Add `generate_codemap()` call at the end of `index_directory()`.
- Replace `query()` method: instead of calling `retrieve()` from intersection.py, call `dispatch()` from dispatch.py. Return the RetrievalPlan.
- Keep `find_path()`, `get_neighbors()`, `get_coverage()` for now (they still work and may be useful for the MCP server). Mark them as legacy with a comment.

### 3B. Update `api.py`

Update to expose the new retrieval interface. The api module should provide:
- `lookup(symbol, builder, **kwargs)` → delegates to `retrieval.lookup.lookup()`
- `search(term, builder, **kwargs)` → delegates to `retrieval.textsearch.text_search()`
- `query(query, builder, **kwargs)` → delegates to `retrieval.dispatch.dispatch()`
- `overview(builder, **kwargs)` → generates Overview from builder

### 3C. Update `mcp_server.py`

Replace the 8 existing MCP tools with 5 new ones:
- `hce_lookup` — calls lookup + traverse
- `hce_search` — calls text_search
- `hce_query` — calls dispatch
- `hce_overview` — generates overview
- `hce_stats` — returns builder.stats()

All tools return `format_json()` output (MCP consumers expect structured data).

### 3D. Move `embeddings.py` to optional Tier 4

Create `retrieval/semantic.py` that wraps EmbeddingManager:

```python
def semantic_search(query, builder, embeddings_path, **kwargs) -> RetrievalPlan:
    """Tier 4: embedding-based fallback.

    Lazy-loads embeddings from disk. If no embeddings exist, computes them
    (requires sentence-transformers to be installed).
    """
```

The EmbeddingManager stays in `graph/embeddings.py` but is no longer imported by pipeline.py by default.

### 3E. Prune dependencies in `pyproject.toml`

```toml
[project]
dependencies = [
    "anthropic>=0.46.0",
    "python-dotenv>=1.0.0",
    "markitdown[all]>=0.1.0",
]

[project.optional-dependencies]
embed = [
    "sentence-transformers>=3.0.0",
    "numpy>=1.26.0",
]
server = [
    "mcp>=1.0.0",
]
text = [
    "pydantic>=2.0.0",
    "instructor>=1.0.0",
]
all = [
    "hypergraph-code-explorer[embed,server,text]",
]
```

Remove from core dependencies:
- `hypernetx` — not used (we have our own builder)
- `networkx` — check if actually imported anywhere; if not, remove
- `sentence-transformers` — move to `[embed]`
- `numpy` — move to `[embed]` (only needed for embeddings)
- `pydantic` + `instructor` — move to `[text]` (only needed for TEXT edge extraction)
- `langchain-text-splitters` — check if chunker.py uses it; if so keep, otherwise remove

Run `grep -r "import networkx\|from networkx\|import hypernetx\|from hypernetx\|import langchain" src/` to verify what's actually imported before removing.

### 3F. Archive old retrieval modules

Don't delete yet — move to an `_archive/` directory or add `# DEPRECATED` headers:
- `retrieval/intersection.py`
- `retrieval/context.py`
- `retrieval/coverage.py`
- `retrieval/pathfinder.py`

The old tests (`test_intersection.py`, `test_coverage.py`, `test_pathfinder.py`, `test_hub_node_filtering.py`) can stay — they test the builder's hub/IDF features which are still used.

### 3G. Update ARCHITECTURE.md

Rewrite to reflect v3 architecture. The data flow, module dependency order, and tool descriptions all need updating. Use PLAN_V3.md as the source of truth.

---

## Execution Order

1. Fix the dispatch.py bug (5 min)
2. Implement `codemap.py` + tests (30 min)
3. Implement `init.py` + tests (20 min)
4. Rewrite `cli.py` with all new commands (45 min)
5. Integration tests for CLI (30 min)
6. Update `pipeline.py` — remove default embeddings, add codemap generation (15 min)
7. Update `api.py` (15 min)
8. Update `mcp_server.py` (20 min)
9. Create `retrieval/semantic.py` for Tier 4 (15 min)
10. Prune dependencies (10 min)
11. Archive old retrieval modules (5 min)
12. Update ARCHITECTURE.md (15 min)
13. Run all tests, fix any failures

## Constraints

- Do NOT modify any KEEP modules (builder.py, code_extractor.py, models.py, converter.py, chunker.py, simplify.py, summaries.py)
- All existing tests must still pass
- No line numbers in any output
- No scoring in any output
- Default output is human-readable text; `--json` flag for structured
- Embeddings must not be required for index or Tiers 1-3
