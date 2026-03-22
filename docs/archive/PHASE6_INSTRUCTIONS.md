# Phase 6: NL Query Precision & Text Search Ranking

Two improvements to retrieval quality, found during integration testing against
Django (1,163 files, 23k nodes, 19k edges).

Run `pytest` after each fix. All 148 existing tests must still pass, plus add
new tests for each fix.

---

## Fix 1: NL Query Token Specificity Filtering

### Problem

The NL query `"how does the ORM build SQL queries"` produces tokens
`['orm', 'build', 'sql', 'queries']` after stopword filtering. The token `sql`
matches a bare module-import node (`sql`) which then gets Tier 2 traversed at
depth 2, pulling in every file that imports `sql` (adapters, backends, GIS
modules, management commands) plus everything *those* files touch. Result:
75 file suggestions for a query that should return ~10 focused results.

The token `build` has the same problem — it's a common identifier that matches
low-value nodes (`build`, `build_q_object`, etc.) and spawns broad traversals.

Root cause: the dispatcher treats every matching token as equally important.
A 3-letter token that matches a bare module import should not get the same
traversal budget as a domain-specific token like `orm` or `queries`.

### Solution: Token Specificity Scoring in Lookup

Add a specificity score to each matched node based on:

1. **Relative degree** — nodes whose degree is a large fraction of the total
   graph are generic. This MUST be relative to graph size, not absolute
   thresholds, so the scoring works on a 500-node project the same as on a
   23k-node project. We learned this lesson with hub node detection (Phase 5)
   — absolute thresholds break across scales.
2. **Token length combined with degree** — short tokens are only penalised
   when they ALSO have high relative degree. A 1-character token `Q` that
   matches Django's `Q` object (low degree, very specific) should score well.
   A 3-character token `sql` that matches a 300-edge module import should not.
   Length alone is not a good signal — it must combine with connectivity.
3. **Match quality** — a direct match on a bare name like `sql` is less
   specific than a segment match on `django.db.models.sql` where the full
   qualified name provides context.

**Design note on generalization:** These thresholds were identified testing
against Django (23k nodes) and FastAPI (1.2k nodes), but the scoring must work
on any Python codebase from 50 to 50,000+ nodes. Use relative/percentile-based
signals, not magic numbers tuned to a specific graph shape.

#### Changes to `retrieval/lookup.py`

Add a `_score_node_specificity` function and use it to rank matched nodes:

```python
def _score_node_specificity(
    node: str,
    token: str,
    builder: HypergraphBuilder,
) -> float:
    """Score how specific a node match is (higher = more specific, more useful).

    Low-specificity matches are common short identifiers that connect broadly
    (like bare module names 'sql', 'os', 'sys'). High-specificity matches are
    domain-specific symbols where the token clearly refers to something
    meaningful ('QuerySet', 'ORM', 'middleware').

    All thresholds are relative to graph size so this works from small
    projects (500 nodes) to large frameworks (23k+ nodes).
    """
    degree = len(builder._node_to_edges.get(node, set()))
    total_edges = max(len(builder._incidence), 1)

    # --- Degree factor (scale-relative) ---
    # What fraction of all edges does this node touch?
    # A node touching >5% of all edges is almost certainly generic.
    # A node touching <0.5% is likely specific.
    degree_ratio = degree / total_edges
    if degree_ratio > 0.05:
        degree_factor = 0.1
    elif degree_ratio > 0.02:
        degree_factor = 0.3
    elif degree_ratio > 0.005:
        degree_factor = 0.6
    else:
        degree_factor = 1.0

    # --- Length factor (only applies when degree is also high) ---
    # Short tokens are ambiguous ONLY when they match high-degree nodes.
    # 'Q' matching Django's Q class (degree 5) is fine.
    # 'sql' matching a 300-edge module import is not.
    # So: length penalty scales with degree_ratio. If the node is specific
    # (low degree_ratio), short tokens get no penalty.
    token_len = len(token)
    if token_len <= 3 and degree_ratio > 0.005:
        # Short token AND moderately connected — penalise
        length_factor = 0.4
    elif token_len <= 3 and degree_ratio > 0.002:
        # Short token, somewhat connected — mild penalty
        length_factor = 0.7
    else:
        # Long token, or short token with low degree — no penalty
        length_factor = 1.0

    # --- Qualified name bonus ---
    # 'django.db.models.sql' is more specific than bare 'sql' regardless
    # of degree, because the qualified path provides disambiguation.
    qualified_factor = 1.0
    if "." in node:
        segments = node.split(".")
        if len(segments) >= 3:
            qualified_factor = 1.3
        elif len(segments) >= 2:
            qualified_factor = 1.1

    return degree_factor * length_factor * qualified_factor
```

#### Changes to `retrieval/dispatch.py`

In `dispatch()`, after Tier 1 lookup, limit the seed nodes passed to Tier 2
based on specificity. Currently (line ~127) it takes `seed_nodes[:5]`. Change
this to rank by specificity and only take seeds above a minimum threshold:

```python
# In dispatch(), after Tier 1 lookup, before Tier 2 traversal:
# Rank seeds by specificity and filter low-value ones
if seed_nodes:
    scored_seeds = [
        (node, _score_node_specificity(node, node.rsplit(".", 1)[-1].lower(), builder))
        for node in seed_nodes
    ]
    scored_seeds.sort(key=lambda x: x[1], reverse=True)

    # Only traverse from seeds above minimum specificity
    MIN_SEED_SPECIFICITY = 0.25
    quality_seeds = [
        node for node, score in scored_seeds
        if score >= MIN_SEED_SPECIFICITY
    ][:5]

    if quality_seeds:
        t2_plan = traverse(
            quality_seeds,
            builder,
            edge_types=edge_types,
            depth=depth,
            direction=direction,
            hub_nodes=hub_nodes,
        )
        plan.merge(t2_plan)
```

Import `_score_node_specificity` from lookup.py at the top of dispatch.py.

Similarly, in the Tier 3 → Tier 1/2 feedback path (line ~147), apply the same
filtering:

```python
# When feeding text search results into traversal:
if text_nodes:
    scored_text = [
        (node, _score_node_specificity(node, node.rsplit(".", 1)[-1].lower(), builder))
        for node in text_nodes
    ]
    scored_text.sort(key=lambda x: x[1], reverse=True)
    quality_text = [
        node for node, score in scored_text
        if score >= MIN_SEED_SPECIFICITY
    ][:3]

    if quality_text:
        t2_sub = traverse(
            quality_text,
            builder,
            edge_types=edge_types,
            depth=min(depth, 1),
            direction=direction,
            hub_nodes=hub_nodes,
        )
        plan.merge(t2_sub)
```

#### Expected Outcome

For `"how does the ORM build SQL queries"` on Django (19k edges):
- `orm` → no direct node match (falls to text search)
- `build` → matches nodes but degree_ratio is moderate; combined with short
  token (5 chars, above the ≤3 threshold) → might survive as seed but traversal
  depth is limited
- `sql` → matches bare `sql` node. Degree ~300 out of 19k edges = ~1.6%
  degree_ratio. Combined with token length 3 (≤3 AND degree_ratio > 0.5%) →
  length_factor penalises → total score drops below threshold → filtered out
  as Tier 2 seed
- `queries` → matches `base.BaseDatabaseWrapper.queries`. Low degree_ratio,
  qualified name bonus → good specificity → used as a seed

The same logic on a SMALL codebase (say 50 edges): if `sql` touches 25 of
50 edges (50% degree_ratio), it scores even worse — the relative threshold
catches it regardless of absolute graph size.

The net result: traversal follows `queries` and any ORM-specific nodes, not
the generic `sql` module. File suggestions should drop from 75 to ~15-20.

#### Tests

Add to `tests/test_lookup.py`:

```python
def test_score_node_specificity_penalises_high_relative_degree():
    """Nodes touching a large fraction of edges should score lower than rare ones.

    This tests the scale-relative property: we create a graph where 'sql'
    touches ~50% of all edges (extremely generic) while 'QuerySet' touches
    only ~0.5% (specific). The scoring should penalise 'sql' regardless of
    absolute graph size.
    """
    builder = HypergraphBuilder()
    # 'sql' touches 200 out of 201 total edges (~99%) — extremely generic
    for i in range(200):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"e{i}", relation=f"imports sql {i}",
            edge_type="IMPORTS", sources=[f"file{i}"], targets=["sql"],
            all_nodes={f"file{i}", "sql"}, source_path=f"f{i}.py",
        ))
    # 'QuerySet' touches 1 out of 201 edges (~0.5%) — very specific
    builder.add_edge(HyperedgeRecord(
        edge_id="e_qs", relation="defines QuerySet",
        edge_type="DEFINES", sources=["query"], targets=["QuerySet"],
        all_nodes={"query", "QuerySet"}, source_path="query.py",
    ))

    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity
    score_sql = _score_node_specificity("sql", "sql", builder)
    score_qs = _score_node_specificity("QuerySet", "queryset", builder)
    assert score_qs > score_sql, f"QuerySet ({score_qs}) should score higher than sql ({score_sql})"


def test_score_node_specificity_scales_with_graph_size():
    """The same degree should score differently depending on total graph size.

    In a 20-edge graph, a node with 5 edges (25%) is generic.
    In a 2000-edge graph, a node with 5 edges (0.25%) is specific.
    """
    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity

    # Small graph: 20 edges total, 'sql' has 5 edges (25%)
    small = HypergraphBuilder()
    for i in range(5):
        small.add_edge(HyperedgeRecord(
            edge_id=f"s_{i}", relation="imports sql",
            edge_type="IMPORTS", sources=[f"f{i}"], targets=["sql"],
            all_nodes={f"f{i}", "sql"}, source_path=f"f{i}.py",
        ))
    for i in range(15):
        small.add_edge(HyperedgeRecord(
            edge_id=f"o_{i}", relation="other",
            edge_type="DEFINES", sources=[f"a{i}"], targets=[f"b{i}"],
            all_nodes={f"a{i}", f"b{i}"}, source_path=f"a{i}.py",
        ))

    # Large graph: 2000 edges total, 'sql' has 5 edges (0.25%)
    large = HypergraphBuilder()
    for i in range(5):
        large.add_edge(HyperedgeRecord(
            edge_id=f"s_{i}", relation="imports sql",
            edge_type="IMPORTS", sources=[f"f{i}"], targets=["sql"],
            all_nodes={f"f{i}", "sql"}, source_path=f"f{i}.py",
        ))
    for i in range(1995):
        large.add_edge(HyperedgeRecord(
            edge_id=f"o_{i}", relation="other",
            edge_type="DEFINES", sources=[f"a{i}"], targets=[f"b{i}"],
            all_nodes={f"a{i}", f"b{i}"}, source_path=f"a{i}.py",
        ))

    score_small = _score_node_specificity("sql", "sql", small)
    score_large = _score_node_specificity("sql", "sql", large)
    assert score_large > score_small, (
        f"Same node with 5 edges should score higher in a 2000-edge graph "
        f"({score_large}) than in a 20-edge graph ({score_small})"
    )


def test_score_node_specificity_short_token_low_degree_no_penalty():
    """Short tokens should NOT be penalised if the node has low relative degree.

    'Q' matching Django's Q class with 5 edges in a 19k-edge graph is fine —
    it's specific. The length penalty should only kick in when combined with
    high relative degree.
    """
    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity

    builder = HypergraphBuilder()
    # 'Q' has 3 edges in a graph with 1000 edges — very specific (0.3%)
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"q_{i}", relation="uses Q",
            edge_type="CALLS", sources=[f"view{i}"], targets=["Q"],
            all_nodes={f"view{i}", "Q"}, source_path=f"view{i}.py",
        ))
    for i in range(997):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"o_{i}", relation="other",
            edge_type="DEFINES", sources=[f"a{i}"], targets=[f"b{i}"],
            all_nodes={f"a{i}", f"b{i}"}, source_path=f"a{i}.py",
        ))

    score_q = _score_node_specificity("Q", "q", builder)
    # Should not be heavily penalised — degree ratio is ~0.3%, well below
    # the threshold where short tokens get penalised
    assert score_q >= 0.8, (
        f"Short token 'q' with low relative degree should score >= 0.8 "
        f"but got {score_q}"
    )


def test_score_node_specificity_qualified_bonus():
    """Qualified names like 'django.db.models.sql' should score higher than bare 'sql'."""
    builder = HypergraphBuilder()
    builder.add_edge(HyperedgeRecord(
        edge_id="e1", relation="test",
        edge_type="IMPORTS", sources=["a"], targets=["sql"],
        all_nodes={"a", "sql"}, source_path="a.py",
    ))
    builder.add_edge(HyperedgeRecord(
        edge_id="e2", relation="test",
        edge_type="IMPORTS", sources=["b"], targets=["django.db.models.sql"],
        all_nodes={"b", "django.db.models.sql"}, source_path="b.py",
    ))

    from hypergraph_code_explorer.retrieval.lookup import _score_node_specificity
    score_bare = _score_node_specificity("sql", "sql", builder)
    score_qualified = _score_node_specificity("django.db.models.sql", "sql", builder)
    assert score_qualified > score_bare
```

Add to `tests/test_dispatch.py` (create if needed):

```python
def test_dispatch_nl_query_filters_low_specificity_seeds():
    """NL queries should not traverse from generic high-degree nodes.

    Simulates the Django 'sql' problem: 'sql' touches ~50% of edges,
    so it should be filtered as a traversal seed even though it matches
    the query token.
    """
    builder = HypergraphBuilder()
    # 'sql' appears in 100 out of 103 edges (~97%) — very generic
    for i in range(100):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"imp_{i}", relation=f"imports sql",
            edge_type="IMPORTS", sources=[f"mod_{i}"], targets=["sql"],
            all_nodes={f"mod_{i}", "sql"}, source_path=f"mod_{i}.py",
        ))
    # 'QuerySet' is specific — only 3 out of 103 edges (~3%)
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"qs_{i}", relation="defines QuerySet",
            edge_type="DEFINES", sources=["query"], targets=["QuerySet"],
            all_nodes={"query", "QuerySet"}, source_path="query.py",
        ))

    plan = dispatch("how does QuerySet use sql", builder)

    # Should NOT have 100+ files from traversing every sql importer
    assert len(plan.primary_files) < 30, (
        f"Expected <30 files but got {len(plan.primary_files)} — "
        f"low-specificity 'sql' node is probably being traversed"
    )


def test_dispatch_specificity_works_at_small_scale():
    """Specificity filtering should work on small graphs too, not just Django-scale.

    In a 30-edge graph, a node with 15 edges (50%) is generic and should
    be filtered, even though 15 is a small absolute number.
    """
    builder = HypergraphBuilder()
    # 'os' touches 15 of 30 edges (50%) — generic even in a small graph
    for i in range(15):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"os_{i}", relation="imports os",
            edge_type="IMPORTS", sources=[f"mod_{i}"], targets=["os"],
            all_nodes={f"mod_{i}", "os"}, source_path=f"mod_{i}.py",
        ))
    # 'Parser' touches 3 of 30 edges (10%) — specific
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"p_{i}", relation="defines Parser",
            edge_type="DEFINES", sources=["parse"], targets=["Parser"],
            all_nodes={"parse", "Parser"}, source_path="parse.py",
        ))
    # Fill in some other edges to make the graph realistic
    for i in range(12):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"other_{i}", relation="other",
            edge_type="CALLS", sources=[f"x_{i}"], targets=[f"y_{i}"],
            all_nodes={f"x_{i}", f"y_{i}"}, source_path=f"x_{i}.py",
        ))

    plan = dispatch("how does Parser use os", builder)

    # Should focus on Parser, not explode through all 15 os-importing files
    assert len(plan.primary_files) < 20, (
        f"Expected <20 files but got {len(plan.primary_files)} — "
        f"small-graph specificity filtering may not be working"
    )
```

---

## Fix 2: Ranked Directory Collapse in Text Search

### Problem

When `search "template"` collapses `django/template/` (9 files) into a single
entry, the agent sees:

```
[2] django/template/  (django.template, Template, ...) -- 9 files match
[2] django/templatetags/  (...) -- 5 files match
[2] django/views/  (...) -- 5 files match
```

All collapsed directories have the same priority (2), so the agent doesn't know
that `django/template/` (9 files, the core template engine) is far more
important than `django/views/` (5 files that merely import template utilities).

### Solution: Weighted Directory Priority

Score collapsed directories based on match density and quality, not just
"priority 2 for everything."

#### Changes to `retrieval/textsearch.py`

Replace the directory collapse section (starting at line 227 `DIR_COLLAPSE_THRESHOLD = 3`)
with a version that computes a quality score per directory:

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
        # Collect all symbols and compute a quality score
        all_symbols: list[str] = []
        total_symbol_count = 0
        for p in paths:
            all_symbols.extend(files_seen[p].symbols)
            total_symbol_count += len(files_seen[p].symbols)
        unique_symbols = list(dict.fromkeys(all_symbols))[:10]

        # Quality score for ranking:
        #   - More matching files = more relevant directory
        #   - More unique symbols = richer match
        #   - Directories whose name contains a search term get a bonus
        dir_name = Path(dir_path).name.lower()
        name_bonus = any(term in dir_name for term in terms)

        # Priority: 1 = best. Lower file_count and no name match → higher
        # priority number (worse rank).
        # Directories with name match AND high file count → priority 1
        # Directories with name match OR high file count → priority 2
        # Everything else → priority 3
        file_count = len(paths)
        if name_bonus and file_count >= 5:
            priority = 1
        elif name_bonus or file_count >= 7:
            priority = 2
        else:
            priority = 3

        collapsed_files.append(FileSuggestion(
            path=dir_path + "/",
            symbols=unique_symbols,
            reason=f"{file_count} files match (text search)",
            priority=priority,
        ))
        collapsed_dirs.add(dir_path)

# Add non-collapsed files
for path, suggestion in files_seen.items():
    dir_path = str(Path(path).parent)
    if dir_path not in collapsed_dirs:
        collapsed_files.append(suggestion)

plan.primary_files = sorted(collapsed_files, key=lambda f: f.priority)
```

#### Expected Outcome

For `search "template"` on Django:

Before:
```
[2] django/template/          -- 9 files match
[2] django/templatetags/      -- 5 files match
[2] django/views/             -- 5 files match
[2] django/template/loaders/  -- 4 files match
[2] django/template/backends/ -- 3 files match
```

After:
```
[1] django/template/          -- 9 files match  (name match + 9 files)
[2] django/templatetags/      -- 5 files match  (name match)
[2] django/template/backends/ -- 3 files match  (name match)
[2] django/template/loaders/  -- 4 files match  (name match)
[3] django/views/             -- 5 files match  (no name match, <7 files)
```

The core `django/template/` directory sorts first because its name matches
the search term and it has the most files. Directories that merely *use*
templates (like `views/`) rank lower.

For `search "migration"`:

Before:
```
[2] django/contrib/auth/migrations/   -- 12 files match
[2] django/contrib/admin/migrations/  -- 3 files match
```

After:
```
[1] django/contrib/auth/migrations/   -- 12 files match  (name match + 12 files)
[2] django/contrib/admin/migrations/  -- 3 files match   (name match)
```

#### Tests

Add to `tests/test_textsearch.py`:

```python
def test_directory_collapse_ranking_name_bonus():
    """Directories whose name matches the search term should rank higher."""
    builder = HypergraphBuilder()
    # Create nodes in a directory whose name matches 'template'
    for i in range(5):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"tpl_{i}", relation=f"template thing {i}",
            edge_type="DEFINES", sources=[f"TemplateClass{i}"], targets=[f"method{i}"],
            all_nodes={f"TemplateClass{i}", f"method{i}"},
            source_path=f"/project/template/file{i}.py",
        ))
    # Create nodes in a directory that merely uses templates
    for i in range(5):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"view_{i}", relation=f"imports template {i}",
            edge_type="IMPORTS", sources=[f"view{i}"], targets=[f"TemplateClass{i}"],
            all_nodes={f"view{i}", f"TemplateClass{i}"},
            source_path=f"/project/views/view{i}.py",
        ))

    plan = text_search("template", builder)

    # Find the collapsed directory entries
    template_dir = next(
        (f for f in plan.primary_files if "template/" in f.path), None
    )
    views_dir = next(
        (f for f in plan.primary_files if "views/" in f.path), None
    )

    assert template_dir is not None, "template/ directory should be in results"
    assert views_dir is not None, "views/ directory should be in results"
    assert template_dir.priority < views_dir.priority, (
        f"template/ dir (priority {template_dir.priority}) should rank higher "
        f"than views/ dir (priority {views_dir.priority})"
    )


def test_directory_collapse_high_file_count_boosts_priority():
    """Directories with many matching files should rank higher."""
    builder = HypergraphBuilder()
    # Directory with 10 matching files
    for i in range(10):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"big_{i}", relation=f"auth thing {i}",
            edge_type="DEFINES", sources=[f"AuthClass{i}"], targets=[f"method{i}"],
            all_nodes={f"AuthClass{i}", f"method{i}"},
            source_path=f"/project/auth/migrations/m{i}.py",
        ))
    # Directory with 3 matching files
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"small_{i}", relation=f"auth thing {i}",
            edge_type="DEFINES", sources=[f"SmallAuth{i}"], targets=[f"func{i}"],
            all_nodes={f"SmallAuth{i}", f"func{i}"},
            source_path=f"/project/other/stuff{i}.py",
        ))

    plan = text_search("auth", builder)

    big_dir = next(
        (f for f in plan.primary_files if "migrations/" in f.path), None
    )
    small_dir = next(
        (f for f in plan.primary_files if "other/" in f.path), None
    )

    assert big_dir is not None
    assert small_dir is not None
    assert big_dir.priority <= small_dir.priority, (
        f"Directory with 10 files (priority {big_dir.priority}) should rank "
        f"equal or higher than directory with 3 files (priority {small_dir.priority})"
    )
```

---

## Verification

After both fixes:

1. `pytest` — all 148+ tests pass (including new ones)

2. Test against Django:
   ```bash
   hce query "how does the ORM build SQL queries" --cache-dir django/.hce_cache
   ```
   Should return ~15-20 files max, focused on `django/db/models/query.py`,
   `django/db/models/sql/`, and `django/db/backends/base/`. Should NOT include
   GIS adapters, management commands, or test files.

3. Test text search ranking:
   ```bash
   hce search "template" --cache-dir django/.hce_cache
   ```
   `django/template/` should appear as priority [1] (first), ahead of
   directories that merely import templates.

4. Regression check — these should still work correctly:
   ```bash
   hce lookup QuerySet --cache-dir django/.hce_cache
   hce lookup FastAPI --calls --cache-dir fastapi/.hce_cache
   hce search "migration" --cache-dir django/.hce_cache
   hce query "how does request validation work" --cache-dir fastapi/.hce_cache
   ```
