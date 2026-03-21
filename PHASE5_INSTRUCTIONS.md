# Phase 5 — Integration Testing Fixes

These are three bugs found during integration testing against real codebases
(requests 18 files, FastAPI 48 files, Django 1163 files / 23k nodes / 19k edges).

All changes must have tests. Run `uv run pytest tests/ -v` after each fix.

---

## Fix 1: Hub Node Detection at Scale

**File:** `src/hypergraph_code_explorer/graph/builder.py`

**Problem:** `get_hub_nodes()` uses a pure percentage threshold (3% of total edges).
At Django scale (19,382 edges), the threshold is 581. Nodes like `len` (498 edges),
`property` (472), `getattr` (467), `hasattr` (439) fall below it and pollute BFS
traversals. At small scale (requests, 485 edges) the threshold is only 14, which works.

**Evidence:**
```
requests  (485 edges,   threshold=14):  11 hubs — isinstance, utils, compat, models, adapters...
FastAPI   (1214 edges,  threshold=36):  13 hubs — Any, Annotated, Doc, utils, __init__, bool...
Django    (19382 edges, threshold=581):  3 hubs — super (1282), isinstance (1012), ValueError (681)
          → len (498), property (472), getattr (467), hasattr (439) are NOT hubs but should be
```

**Fix:** Use a hybrid approach: hub if degree exceeds EITHER the percentage threshold
OR a fixed minimum floor. The floor catches builtins/generics in large graphs where
percentage alone is too high.

**Current code** (builder.py, `get_hub_nodes` method):
```python
def get_hub_nodes(self, max_degree_pct: float = 0.03) -> set[str]:
    total_edges = len(self._incidence)
    threshold = max(2, int(total_edges * max_degree_pct))
    return {
        node for node, edge_ids in self._node_to_edges.items()
        if len(edge_ids) > threshold
    }
```

**Replace with:**
```python
def get_hub_nodes(self, max_degree_pct: float = 0.03, min_degree_floor: int = 50) -> set[str]:
    """Return nodes whose degree exceeds max_degree_pct * total_edges OR min_degree_floor.

    These "hub" nodes appear in so many edges that they create
    spurious adjacency connections. Uses a hybrid threshold:
      - Percentage-based: adapts to graph size (3% of edges)
      - Fixed floor: catches builtins/generics in large graphs
    The LOWER of the two thresholds is used (i.e., more aggressive filtering).

    Examples:
      - 500 edges -> pct threshold = 15, floor = 50, effective = 15
      - 20,000 edges -> pct threshold = 600, floor = 50, effective = 50
    """
    total_edges = len(self._incidence)
    pct_threshold = max(2, int(total_edges * max_degree_pct))
    threshold = min(pct_threshold, min_degree_floor)
    return {
        node for node, edge_ids in self._node_to_edges.items()
        if len(edge_ids) > threshold
    }
```

**Why `min()` not `max()`:** We want the MORE aggressive filter. For small graphs,
the percentage (14) is already below the floor (50), so we use 14. For large graphs,
the percentage (581) is way above the floor, so we use 50 to catch `len`/`property`/etc.

**Test:** Add a test that constructs a builder with a known hub pattern and verifies
both thresholds work. Also test that for a small graph (<50 edges), the percentage
threshold is used (not the floor). Example:

```python
def test_hub_node_floor():
    """At scale, the fixed floor catches builtins the percentage misses."""
    b = HypergraphBuilder()
    # Create a large graph: 2000 edges, one node in 100 of them
    for i in range(2000):
        b.add_edge(f"edge_{i}", "CALLS", [f"func_{i}"], [f"target_{i}"],
                   source_path=f"file_{i}.py", relation=f"calls {i}")
    # Add a "builtin" node to 100 edges
    for i in range(100):
        b.add_edge(f"builtin_edge_{i}", "CALLS", ["isinstance"], [f"target_{i}"],
                   source_path=f"file_{i}.py", relation="isinstance call")
    hubs = b.get_hub_nodes()
    assert "isinstance" in hubs  # 100 > floor of 50

def test_hub_node_small_graph():
    """For small graphs, percentage threshold is still used."""
    b = HypergraphBuilder()
    for i in range(100):
        b.add_edge(f"edge_{i}", "CALLS", [f"func_{i}"], [f"target_{i}"],
                   source_path=f"file.py", relation=f"calls {i}")
    # Node in 4 edges — above 3% of 100 = 3
    for i in range(4):
        b.add_edge(f"hub_edge_{i}", "CALLS", ["common"], [f"x_{i}"],
                   source_path="file.py", relation="common call")
    hubs = b.get_hub_nodes()
    assert "common" in hubs  # 4 > 3% of 100 = 3
```

---

## Fix 2: `--calls` on Class Nodes Should Expand to Methods

**File:** `src/hypergraph_code_explorer/cli.py` (function `_run_lookup`)

**Problem:** `hce lookup FastAPI --calls` returns nothing because the `FastAPI`
class node only has DEFINES and INHERITS edges — CALLS edges live on its methods
like `applications.FastAPI.__init__`. An agent asking "what does FastAPI call?"
expects to see the calls made by its methods.

**Evidence:**
```
$ hce lookup FastAPI --calls
Query: FastAPI
Tiers used: [1, 2]
=== Grep Suggestions ===
  grep -rn 'FastAPI'
=== Context ===
Found 2 node(s) matching query: fastapi, applications.FastAPI
```
No files, no symbols, no call chains — useless.

**Fix:** In `_run_lookup`, after the initial lookup, if edge_types is filtered to
CALLS and the matched nodes have DEFINES edges pointing to child methods, expand
the seed nodes to include those methods before running Tier 2 traversal.

**Current code** (cli.py, `_run_lookup`, around line 230-239):
```python
plan = lookup(args.symbol, builder, edge_types=edge_types)

# If depth > 0, run Tier 2 traversal from the looked-up nodes
if args.depth > 0 and not plan.is_empty():
    seed_nodes = list({s.name for s in plan.related_symbols})
    t2 = traverse(
        seed_nodes[:5], builder,
        edge_types=edge_types, depth=args.depth, direction=direction,
    )
    plan.merge(t2)
```

**Replace with:**
```python
plan = lookup(args.symbol, builder, edge_types=edge_types)

# If the lookup returned no actionable results (no files) but would have
# results without the edge_type filter, this is likely a class node where
# CALLS edges live on child methods. Expand by looking up the class without
# the filter, finding DEFINES targets, then looking those up with the filter.
if plan.is_empty() or not plan.primary_files:
    # Try unfiltered lookup to find the class definition
    unfiltered = lookup(args.symbol, builder, edge_types=None)
    if not unfiltered.is_empty():
        # Find child nodes via DEFINES edges
        child_nodes: list[str] = []
        for sym in unfiltered.related_symbols:
            if sym.relationship == "defines" and sym.targets:
                child_nodes.extend(sym.targets)
        if child_nodes and edge_types:
            # Re-run lookup on child methods with the edge type filter
            for child in child_nodes[:20]:
                child_plan = lookup(child, builder, edge_types=edge_types)
                plan.merge(child_plan)
            # Also merge the unfiltered plan for context (class definition, inheritance)
            plan.merge(unfiltered)

# If depth > 0, run Tier 2 traversal from the looked-up nodes
if args.depth > 0 and not plan.is_empty():
    seed_nodes = list({s.name for s in plan.related_symbols})
    t2 = traverse(
        seed_nodes[:5], builder,
        edge_types=edge_types, depth=args.depth, direction=direction,
    )
    plan.merge(t2)
```

**Expected result after fix:**
```
$ hce lookup FastAPI --calls
→ applications.FastAPI defines __init__, build_middleware_stack, openapi, ...
→ applications.FastAPI.__init__ calls Starlette.__init__, ...
→ applications.FastAPI.build_middleware_stack calls ServerErrorMiddleware, ...
→ etc.
```

**Test:** Test with a builder containing a class with DEFINES edges pointing to
methods that have CALLS edges. Verify that `--calls` on the class returns the
methods' calls.

---

## Fix 3: Text Search Directory Grouping at Scale

**File:** `src/hypergraph_code_explorer/retrieval/textsearch.py`

**Problem:** `hce search "migration"` on Django returns 27 individual migration files
(`0001_initial.py`, `0002_alter_permission...py`, etc.) when what an agent needs is
"look in `django/db/migrations/`". At scale, text search results dominated by many
files in the same directory are noisy and waste context window.

**Fix:** In `text_search()`, after collecting file suggestions, group files that share
a common directory. If 3+ files from the same directory match, collapse them into a
single directory-level suggestion with a reason like "5 files in django/db/migrations/
match 'migration'".

**Implementation location:** In `text_search()` function, after building `files_seen`
dict (around line 218), before assigning to `plan.primary_files`:

```python
# Group files by directory — if 3+ files from same dir, collapse into one entry
DIR_COLLAPSE_THRESHOLD = 3
dir_counts: dict[str, list[str]] = defaultdict(list)
for path in files_seen:
    dir_path = str(Path(path).parent)
    dir_counts[dir_path].append(path)

collapsed_files: list[FileSuggestion] = []
collapsed_dirs: set[str] = set()
for dir_path, paths in dir_counts.items():
    if len(paths) >= DIR_COLLAPSE_THRESHOLD:
        # Collapse into directory suggestion
        all_symbols: list[str] = []
        for p in paths:
            all_symbols.extend(files_seen[p].symbols)
        # Keep only unique symbols, limit to first 10
        unique_symbols = list(dict.fromkeys(all_symbols))[:10]
        collapsed_files.append(FileSuggestion(
            path=dir_path + "/",
            symbols=unique_symbols,
            reason=f"{len(paths)} files match (text search)",
            priority=2,
        ))
        collapsed_dirs.add(dir_path)

# Add non-collapsed files
for path, suggestion in files_seen.items():
    dir_path = str(Path(path).parent)
    if dir_path not in collapsed_dirs:
        collapsed_files.append(suggestion)

plan.primary_files = sorted(collapsed_files, key=lambda f: f.priority)
```

Replace the existing line:
```python
plan.primary_files = sorted(files_seen.values(), key=lambda f: f.priority)
```

**Test:** Test with a builder containing 5+ files from the same directory all matching
a search term. Verify the output collapses to one directory entry. Also verify that
files from different directories are NOT collapsed.

---

## Verification

After all three fixes, run the full test suite:
```bash
uv run pytest tests/ -v
```

Then run these integration tests manually to verify against real indexed codebases
(you can index them with `hce index <path> --skip-summaries`):

```bash
# Hub node fix — Django should have more hubs now
hce stats --cache-dir /path/to/django/.hce_cache
# Expect hub_nodes: ~20-40 instead of 3

# Class expansion fix — FastAPI
hce lookup FastAPI --calls --cache-dir /path/to/fastapi/.hce_cache
# Expect: method-level CALLS shown, not empty results

# Directory collapse fix — Django
hce search "migration" --cache-dir /path/to/django/.hce_cache
# Expect: collapsed directory entries instead of 27 individual files
```
