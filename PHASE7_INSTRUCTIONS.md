# Phase 7: Directory Collapse for Dispatch Output

One fix. Apply the directory collapse logic (already working in Tier 3 text
search) to the final merged output of `dispatch()`, so that Tier 1 lookup +
Tier 2 traversal results also benefit from grouping.

Run `pytest` after. All existing tests (156+) must still pass, plus add new
tests for this fix.

---

## Background: Why This Is Needed

Phase 6 Fix 1 (token specificity scoring) was designed to solve a problem where
`hce query "how does the ORM build SQL queries"` returns 57+ files against
Django. The hypothesis was that `sql` is a generic high-degree hub node
dragging in irrelevant results.

**That hypothesis was wrong.** In Django's graph (19,382 edges), `sql` has only
11 edges — a degree ratio of 0.06%. It's genuinely rare and the specificity
filter correctly gives it a score of 1.0. The filter works as designed; `sql`
just isn't the kind of node it was built to catch.

The real problem is structural:

1. Tier 1 lookup finds ~15 files that reference the `sql` node (files that
   import it, define things in `django/db/models/sql/`, etc.)
2. Those files become Tier 2 traversal seeds. Each seed fans out at depth 2.
3. 15 seeds x ~4 new files each = ~57 total files.

The explosion comes from having too many starting points, not from one
high-degree node. Many of those 57 files are in the same directories:
`django/db/models/sql/compiler.py`, `django/db/models/sql/query.py`,
`django/db/models/sql/where.py`, etc.

Directory collapse already solves this problem in Tier 3 text search
(`textsearch.py` lines 226-277). We need to apply the same logic to the final
dispatch output after all tiers merge.

**Phase 6 Fix 1 (specificity scoring) stays in place.** It provides real value
for codebases where short tokens DO match high-degree hub nodes. It just didn't
solve this particular case.

---

## Fix: Directory Collapse on Final Dispatch Output

### Changes to `retrieval/dispatch.py`

At the end of `dispatch()`, after the final `plan.primary_files.sort()` on
line 228, apply directory collapse to the merged results. This replaces the
simple sort with a collapse-then-sort.

Replace lines 227-230:

```python
    # Sort files by priority
    plan.primary_files.sort(key=lambda f: f.priority)

    return plan
```

With:

```python
    # --- Directory collapse on final output ---
    # When multiple tiers contribute files from the same directory,
    # collapse them into a single directory entry (same logic as Tier 3
    # text search). This prevents the agent from seeing 57 individual
    # files when 8 of them are in django/db/models/sql/ and could be
    # shown as one directory entry.
    plan.primary_files = _collapse_directories(plan.primary_files, query)

    return plan
```

Add a new helper function `_collapse_directories` at module level in
`dispatch.py`, above `dispatch()`:

```python
def _collapse_directories(
    files: list[FileSuggestion],
    query: str,
) -> list[FileSuggestion]:
    """Collapse files from the same directory into single directory entries.

    When 3+ files share a parent directory, they are replaced by one
    FileSuggestion whose path ends with '/' and whose reason shows the
    file count. Collapsed directories are ranked by name relevance and
    file count, matching the Tier 3 text search behaviour.

    Files whose path already ends with '/' (i.e. already collapsed by
    Tier 3) are passed through as-is and their directory is excluded
    from further collapsing.
    """
    from collections import defaultdict
    from pathlib import Path

    DIR_COLLAPSE_THRESHOLD = 3

    # Extract query terms for name-match scoring (same logic as textsearch)
    terms = _extract_dispatch_terms(query)

    # Separate already-collapsed directory entries from individual files
    already_collapsed: list[FileSuggestion] = []
    individual_files: list[FileSuggestion] = []
    already_collapsed_dirs: set[str] = set()

    for f in files:
        if f.path.endswith("/"):
            already_collapsed.append(f)
            # Track the dir so we don't re-collapse files under it
            already_collapsed_dirs.add(f.path.rstrip("/"))
        else:
            individual_files.append(f)

    # Group individual files by parent directory
    dir_groups: dict[str, list[FileSuggestion]] = defaultdict(list)
    for f in individual_files:
        dir_path = str(Path(f.path).parent)
        # Don't group files whose directory was already collapsed by Tier 3
        if dir_path in already_collapsed_dirs:
            continue
        dir_groups[dir_path].append(f)

    # Collapse directories that meet the threshold
    collapsed: list[FileSuggestion] = []
    collapsed_dirs: set[str] = set()

    for dir_path, dir_files in dir_groups.items():
        if len(dir_files) >= DIR_COLLAPSE_THRESHOLD:
            # Gather symbols from all files in this directory
            all_symbols: list[str] = []
            best_priority = min(f.priority for f in dir_files)
            for f in dir_files:
                for s in f.symbols:
                    if s not in all_symbols:
                        all_symbols.append(s)
            unique_symbols = all_symbols[:10]

            # Rank the collapsed directory using name match + file count
            dir_name = Path(dir_path).name.lower()
            name_bonus = any(term in dir_name for term in terms)
            file_count = len(dir_files)

            if name_bonus and file_count >= 5:
                priority = 1
            elif name_bonus or file_count >= 7:
                priority = 2
            else:
                # Use the best priority from the constituent files,
                # but floor at 3 so collapsed dirs without name match
                # don't outrank individually important files
                priority = max(best_priority, 3)

            collapsed.append(FileSuggestion(
                path=dir_path + "/",
                symbols=unique_symbols,
                reason=f"{file_count} files match",
                priority=priority,
            ))
            collapsed_dirs.add(dir_path)

    # Collect non-collapsed individual files
    remaining: list[FileSuggestion] = []
    for f in individual_files:
        dir_path = str(Path(f.path).parent)
        if dir_path not in collapsed_dirs and dir_path not in already_collapsed_dirs:
            remaining.append(f)

    # Combine: already-collapsed (from Tier 3) + newly collapsed + remaining individuals
    result = already_collapsed + collapsed + remaining
    result.sort(key=lambda f: f.priority)
    return result


def _extract_dispatch_terms(query: str) -> list[str]:
    """Extract search terms from a query for directory name matching.

    Mirrors the stopword filtering in textsearch._extract_search_terms
    but kept separate to avoid circular imports.
    """
    import re

    STOPWORDS = {
        "how", "does", "what", "why", "when", "where", "which",
        "the", "and", "for", "that", "this", "with", "from", "into",
        "use", "uses", "used", "get", "set", "has", "have", "can",
        "will", "would", "should", "each", "some", "any", "are",
        "was", "were", "been", "being", "about", "work", "works",
        "all", "its", "not", "but", "they", "them", "their", "there",
    }
    raw = re.split(r'[\s,;:!?(){}\[\]"\'`/\\]+', query)
    terms: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        for part in re.split(r'[._]', tok):
            low = part.lower().strip()
            if low and low not in seen and low not in STOPWORDS and len(low) >= 3:
                seen.add(low)
                terms.append(low)
    return terms
```

Add the necessary import at the top of `dispatch.py`:

```python
from .plan import RetrievalPlan, FileSuggestion
```

(`FileSuggestion` is needed for the type annotation in `_collapse_directories`.
`RetrievalPlan` is already imported.)

### Important Implementation Notes

1. **Already-collapsed entries pass through.** Tier 3 text search already
   collapses its own results before merging. When `plan.merge()` adds those
   directory entries (paths ending in `/`), `_collapse_directories` must
   recognise them and not try to re-collapse files under those directories.
   The function handles this via the `already_collapsed_dirs` set.

2. **Priority interaction.** Individual files from Tier 1 have `priority=1`.
   Collapsed directories without a name match get `priority=max(best_priority, 3)`,
   meaning they don't outrank individual high-priority files that didn't get
   collapsed. But directories WITH a name match can earn `priority=1` or `2`,
   which is correct — `django/db/models/sql/` should rank highly for a query
   about SQL.

3. **No circular imports.** `_extract_dispatch_terms` duplicates the stopword
   list from `textsearch._extract_search_terms` rather than importing it.
   This avoids a circular import since textsearch already imports from plan,
   and dispatch imports from textsearch. If you prefer, you can extract the
   stopword list into a shared `constants.py` module, but duplication is
   acceptable for now.

4. **`merge()` deduplicates by path.** The `RetrievalPlan.merge()` method
   (plan.py line 136) skips files whose path is already in the plan. This
   means Tier 3's collapsed `django/db/models/sql/` entry and Tier 1's
   individual `django/db/models/sql/compiler.py` entry will BOTH be in the
   plan (different paths). `_collapse_directories` handles this: the
   already-collapsed `/` entry is preserved, and the individual file under
   that directory is excluded from further collapsing via `already_collapsed_dirs`.

---

## Expected Outcome

For `hce query "how does the ORM build SQL queries" --cache-dir django/.hce_cache`:

Before (current behaviour — 57 files):
```
  [1] django/db/models/sql/compiler.py  (SQLCompiler, ...)
  [1] django/db/models/sql/query.py     (Query, ...)
  [1] django/db/models/sql/where.py     (WhereNode, ...)
  [1] django/db/models/sql/subqueries.py
  [1] django/db/models/sql/constants.py
  [1] django/db/models/sql/datastructures.py
  [1] django/db/models/query.py         (QuerySet, ...)
  [1] django/db/backends/base/operations.py
  [1] django/db/backends/sqlite3/operations.py
  [1] django/db/backends/postgresql/operations.py
  ... (47 more files)
```

After (collapsed — ~10-15 entries):
```
  [1] django/db/models/sql/            (SQLCompiler, Query, WhereNode, ...) -- 6 files match
  [1] django/db/models/query.py        (QuerySet, ...)
  [2] django/db/backends/base/         (BaseDatabaseOperations, ...) -- 4 files match
  [3] django/db/backends/sqlite3/      (...) -- 3 files match
  [3] django/db/backends/postgresql/   (...) -- 3 files match
  ... (a few more)
```

The `sql/` directory gets `priority=1` because:
- `dir_name = "sql"` → `name_bonus = True` (the term `sql` is in the query)
- `file_count = 6` → `>= 5`
- Both conditions met → `priority = 1`

The agent now sees a concise, navigable list instead of 57 flat files.

---

## Tests

Add to `tests/test_dispatch.py`:

```python
from collections import defaultdict
from pathlib import Path

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.dispatch import (
    dispatch,
    _collapse_directories,
    _extract_dispatch_terms,
)
from hypergraph_code_explorer.retrieval.plan import FileSuggestion


def test_collapse_directories_groups_same_dir():
    """When 3+ files share a parent directory, they should collapse into one entry."""
    files = [
        FileSuggestion(path="/proj/models/sql/compiler.py", symbols=["SQLCompiler"], reason="lookup", priority=1),
        FileSuggestion(path="/proj/models/sql/query.py", symbols=["Query"], reason="lookup", priority=1),
        FileSuggestion(path="/proj/models/sql/where.py", symbols=["WhereNode"], reason="lookup", priority=1),
        FileSuggestion(path="/proj/models/query.py", symbols=["QuerySet"], reason="lookup", priority=1),
    ]
    result = _collapse_directories(files, "how does sql work")

    # The 3 sql/ files should collapse into one directory entry
    dir_entries = [f for f in result if f.path.endswith("/")]
    assert len(dir_entries) == 1
    assert dir_entries[0].path == "/proj/models/sql/"
    assert "3 files match" in dir_entries[0].reason

    # query.py should remain as an individual file
    individual = [f for f in result if not f.path.endswith("/")]
    assert any("query.py" in f.path for f in individual)


def test_collapse_directories_name_bonus_priority():
    """Collapsed directories whose name matches a query term get better priority."""
    files = [
        # 5 files in sql/ directory — name matches query term "sql"
        FileSuggestion(path=f"/proj/sql/f{i}.py", symbols=[f"Sym{i}"], reason="t", priority=1)
        for i in range(5)
    ] + [
        # 5 files in utils/ directory — name does NOT match
        FileSuggestion(path=f"/proj/utils/f{i}.py", symbols=[f"Util{i}"], reason="t", priority=1)
        for i in range(5)
    ]
    result = _collapse_directories(files, "how does sql work")

    sql_dir = next(f for f in result if "sql/" in f.path)
    utils_dir = next(f for f in result if "utils/" in f.path)

    # sql/ should get priority 1 (name match + 5 files)
    assert sql_dir.priority == 1, f"sql/ got priority {sql_dir.priority}, expected 1"
    # utils/ should get priority 3 (no name match, <7 files)
    assert utils_dir.priority == 3, f"utils/ got priority {utils_dir.priority}, expected 3"
    # sql/ should sort before utils/
    sql_idx = result.index(sql_dir)
    utils_idx = result.index(utils_dir)
    assert sql_idx < utils_idx


def test_collapse_directories_preserves_already_collapsed():
    """Entries already collapsed by Tier 3 (path ends with /) should pass through unchanged."""
    files = [
        # Already collapsed by Tier 3
        FileSuggestion(path="/proj/template/", symbols=["Template"], reason="9 files match (text search)", priority=1),
        # Individual file under the same directory — should NOT cause double-collapse
        FileSuggestion(path="/proj/template/base.py", symbols=["BaseEngine"], reason="lookup", priority=1),
        # Individual file elsewhere
        FileSuggestion(path="/proj/views/index.py", symbols=["index"], reason="lookup", priority=2),
    ]
    result = _collapse_directories(files, "template rendering")

    # The already-collapsed entry should still be there
    collapsed = [f for f in result if f.path == "/proj/template/"]
    assert len(collapsed) == 1
    assert collapsed[0].reason == "9 files match (text search)"  # unchanged

    # base.py should be excluded (its directory is already collapsed)
    base_files = [f for f in result if "base.py" in f.path]
    assert len(base_files) == 0

    # views/index.py should still be there (different directory)
    view_files = [f for f in result if "index.py" in f.path]
    assert len(view_files) == 1


def test_collapse_directories_below_threshold_not_collapsed():
    """Directories with fewer than 3 files should NOT be collapsed."""
    files = [
        FileSuggestion(path="/proj/sql/a.py", symbols=["A"], reason="t", priority=1),
        FileSuggestion(path="/proj/sql/b.py", symbols=["B"], reason="t", priority=1),
        FileSuggestion(path="/proj/other/c.py", symbols=["C"], reason="t", priority=1),
    ]
    result = _collapse_directories(files, "sql query")

    # No directories should be collapsed (sql/ has only 2 files)
    dir_entries = [f for f in result if f.path.endswith("/")]
    assert len(dir_entries) == 0
    assert len(result) == 3


def test_collapse_directories_collects_symbols():
    """Collapsed directory entry should contain symbols from all constituent files."""
    files = [
        FileSuggestion(path="/proj/sql/a.py", symbols=["Query", "SubQuery"], reason="t", priority=1),
        FileSuggestion(path="/proj/sql/b.py", symbols=["WhereNode"], reason="t", priority=1),
        FileSuggestion(path="/proj/sql/c.py", symbols=["Query", "Compiler"], reason="t", priority=1),
    ]
    result = _collapse_directories(files, "sql")

    dir_entry = next(f for f in result if f.path.endswith("/"))
    # Should have deduplicated symbols: Query, SubQuery, WhereNode, Compiler
    assert "Query" in dir_entry.symbols
    assert "WhereNode" in dir_entry.symbols
    assert "Compiler" in dir_entry.symbols
    # "Query" should appear only once despite being in two files
    assert dir_entry.symbols.count("Query") == 1


def test_dispatch_end_to_end_directory_collapse():
    """Full dispatch should collapse Tier 1+2 results from the same directory.

    Simulates the Django ORM query problem: many files in the same
    directory get pulled in by Tier 1 lookup + Tier 2 traversal.
    After collapse, the agent should see directory entries instead of
    a flat list of 20+ files.
    """
    builder = HypergraphBuilder()

    # Create a cluster of files in /proj/models/sql/ that all relate to 'sql'
    sql_files = ["compiler", "query", "where", "subqueries", "constants", "datastructures"]
    for i, name in enumerate(sql_files):
        # Each file defines a class and imports sql
        builder.add_edge(HyperedgeRecord(
            edge_id=f"sql_def_{i}", relation=f"defines {name.title()}",
            edge_type="DEFINES", sources=[f"sql.{name}"], targets=[name.title()],
            all_nodes={f"sql.{name}", name.title()},
            source_path=f"/proj/models/sql/{name}.py",
        ))
        builder.add_edge(HyperedgeRecord(
            edge_id=f"sql_imp_{i}", relation="imports sql",
            edge_type="IMPORTS", sources=[f"sql.{name}"], targets=["sql"],
            all_nodes={f"sql.{name}", "sql"},
            source_path=f"/proj/models/sql/{name}.py",
        ))

    # Create some files in /proj/backends/ that also import sql
    for i in range(4):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"backend_{i}", relation="imports sql",
            edge_type="IMPORTS", sources=[f"backend_{i}"], targets=["sql"],
            all_nodes={f"backend_{i}", "sql"},
            source_path=f"/proj/backends/db{i}.py",
        ))

    # Pad with unrelated edges so specificity thresholds don't distort
    for i in range(80):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"pad_{i}", relation="other",
            edge_type="DEFINES", sources=[f"x_{i}"], targets=[f"y_{i}"],
            all_nodes={f"x_{i}", f"y_{i}"},
            source_path=f"/proj/other/f{i}.py",
        ))

    plan = dispatch("how does sql work", builder)

    # After collapse, we should see directory entries, not 10+ individual files
    dir_entries = [f for f in plan.primary_files if f.path.endswith("/")]
    individual_entries = [f for f in plan.primary_files if not f.path.endswith("/")]

    # The 6 sql/ files should collapse into one directory entry
    sql_dirs = [f for f in dir_entries if "sql" in f.path]
    assert len(sql_dirs) >= 1, (
        f"Expected at least one sql/ directory entry, got dirs: "
        f"{[f.path for f in dir_entries]}"
    )

    # Total entries should be significantly fewer than total individual files
    # that would have been returned without collapse
    assert len(plan.primary_files) < 15, (
        f"Expected <15 entries after collapse, got {len(plan.primary_files)}: "
        f"{[f.path for f in plan.primary_files]}"
    )


def test_extract_dispatch_terms():
    """Verify term extraction filters stopwords and short tokens."""
    terms = _extract_dispatch_terms("how does the ORM build SQL queries")
    assert "orm" in terms
    assert "build" in terms
    assert "sql" in terms
    assert "queries" in terms
    # Stopwords should be filtered
    assert "how" not in terms
    assert "does" not in terms
    assert "the" not in terms
```

---

## Verification

After implementing:

1. `pytest` — all 156+ existing tests pass, plus the new tests above.

2. Test against Django:
   ```bash
   hce query "how does the ORM build SQL queries" --cache-dir django/.hce_cache
   ```
   Should return ~10-15 entries (with directory entries like `django/db/models/sql/`)
   instead of 57 individual files.

3. Regression checks — these should still work correctly:
   ```bash
   hce lookup QuerySet --cache-dir django/.hce_cache
   hce lookup FastAPI --calls --cache-dir fastapi/.hce_cache
   hce search "template" --cache-dir django/.hce_cache
   hce search "migration" --cache-dir django/.hce_cache
   hce query "how does request validation work" --cache-dir fastapi/.hce_cache
   ```

4. Verify the small-graph guard: On FastAPI (1,264 nodes, small enough that
   most queries return <20 files), collapse should rarely trigger and results
   should look identical to before.
