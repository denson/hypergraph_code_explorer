# Phase 8: Test-Path Deprioritization

One fix with two parts. Deprioritize test files so source code ranks above
tests in retrieval results. Tests remain accessible but don't crowd out the
implementation code an agent needs to understand first.

Run `pytest` after. All existing tests (163+) must still pass, plus add new
tests for this fix.

---

## Background: Why This Is Needed

When indexing the full Django repo (2,810 files, 63,588 edges), **67% of files
and 70% of edges come from test code.** Test files import the same symbols as
source files, define the same class names (every migration test fixture defines
a `Migration` class), and call the same functions (every view test calls
`render`, `Template`, `HttpRequest`).

The result: queries return test files at the same priority as source files.
For `"how does the migration system detect changes"`, 105 of 123 results are
test directories. For `"how does the template engine render templates"`, 57 of
86 results are tests. The agent sees test fixtures before it sees the actual
implementation.

Phase 7 (directory collapse) helps by grouping `tests/migrations/test_foo/`
directories, but they still rank at priority 1-2 alongside source code.

The fix: apply a priority penalty to files under test directories so they sort
below source files. The agent sees source code first, tests after.

---

## Fix Part A: `_is_test_path` Helper

Add a helper function to `dispatch.py` that detects test file paths using
common conventions. This must work across different project layouts.

Add this function in the "Directory collapse helpers" section (after
`_extract_dispatch_terms`, before `_collapse_directories`):

```python
# Priority penalty applied to test files so source code sorts first.
# Tests remain in results (they're useful for understanding usage patterns)
# but at lower priority than implementation files.
TEST_PATH_PRIORITY_PENALTY = 2


def _is_test_path(path: str) -> bool:
    """Detect whether a file path is a test file.

    Matches common Python project conventions:
      - Files under a 'tests/' or 'test/' directory
      - Files named test_*.py or *_test.py
      - conftest.py files
      - Files under directories like 'testing/', 'test_*/'

    Works on both individual file paths and collapsed directory paths
    (ending with '/').
    """
    # Normalize to forward slashes
    normalized = path.replace("\\", "/").lower()

    # Split into path segments
    parts = normalized.rstrip("/").split("/")

    for part in parts:
        # Directory named 'tests' or 'test'
        if part in ("tests", "test"):
            return True
        # Directory starting with 'test_' (e.g. test_migrations_plan/)
        if part.startswith("test_"):
            return True

    # Filename checks (only for non-directory paths)
    if not path.endswith("/") and parts:
        filename = parts[-1]
        if filename.startswith("test_") or filename.endswith("_test.py"):
            return True
        if filename in ("conftest.py", "testing.py"):
            return True

    return False
```

## Fix Part B: Apply Penalty in `_collapse_directories`

Modify `_collapse_directories` to apply the test-path penalty AFTER
directory collapse but BEFORE the final sort. This way:
- Test directories that collapse still get the penalty
- Individual test files that didn't collapse also get the penalty
- The penalty stacks with the existing priority (a priority-1 test file
  becomes priority-3, a priority-2 becomes priority-4)

At the end of `_collapse_directories`, just before the `result.sort(...)` line,
add the test-path penalty:

Replace:

```python
    # Combine: already-collapsed (from Tier 3) + newly collapsed + remaining individuals
    result = already_collapsed + collapsed + remaining
    result.sort(key=lambda f: f.priority)
    return result
```

With:

```python
    # Combine: already-collapsed (from Tier 3) + newly collapsed + remaining individuals
    result = already_collapsed + collapsed + remaining

    # Apply test-path priority penalty so source files sort before tests
    for f in result:
        if _is_test_path(f.path):
            f.priority += TEST_PATH_PRIORITY_PENALTY

    result.sort(key=lambda f: f.priority)
    return result
```

## Fix Part C: Source-Preferred Tier 2 Seed Selection

In `dispatch()`, when selecting Tier 2 traversal seeds from Tier 1 results,
prefer seeds that come from source files. This keeps traversal focused on
implementation code rather than fanning out through test infrastructure.

In the Tier 2 seed selection section (around line 285-303), after building
`seed_nodes` from `t1_plan.related_symbols`, add source-path preference.

Currently the code builds `seed_nodes` from symbols:

```python
            seed_nodes = []
            seen: set[str] = set()
            for sym in t1_plan.related_symbols:
                if sym.name not in seen:
                    seed_nodes.append(sym.name)
                    seen.add(sym.name)
```

Replace with:

```python
            # Collect seed nodes, preferring those from source files
            # over test files. This keeps Tier 2 traversal focused on
            # implementation rather than fanning through test infrastructure.
            source_seeds: list[str] = []
            test_seeds: list[str] = []
            seen: set[str] = set()
            for sym in t1_plan.related_symbols:
                if sym.name not in seen:
                    seen.add(sym.name)
                    if _is_test_path(sym.file):
                        test_seeds.append(sym.name)
                    else:
                        source_seeds.append(sym.name)
            # Source seeds first, then test seeds as fallback
            seed_nodes = source_seeds + test_seeds
```

This ensures that when we take `[:5]` seeds for traversal, we prefer seeds
from source files. If there are 3 source seeds and 2 test seeds, the 5-seed
limit takes all 3 source seeds before any test seeds. If there are 0 source
seeds (rare), test seeds still provide traversal.

Apply the same pattern to the Tier 3 → Tier 2 feedback path (around line 331):

Currently:

```python
            text_nodes = [s.name for s in t3_plan.related_symbols[:5]]
```

Replace with:

```python
            # Prefer source-file symbols for structural expansion
            src_text = [s.name for s in t3_plan.related_symbols
                        if not _is_test_path(s.file)]
            test_text = [s.name for s in t3_plan.related_symbols
                         if _is_test_path(s.file)]
            text_nodes = (src_text + test_text)[:5]
```

---

## Important Design Notes

1. **Tests are NOT removed.** They're deprioritized. An agent exploring
   Django's ORM will see `django/db/models/sql/` at priority 2 and
   `tests/queries/` at priority 4. If it needs test examples, they're still
   in the results — just below the implementation.

2. **The penalty is +2, not +10.** A test file at priority-1 becomes
   priority-3, which is the same as a source-code collapsed directory without
   a name match. This feels right: an important test (like the ORM test that
   directly calls `SQLCompiler`) should rank roughly even with a tangentially-
   related source directory. If the penalty were too high, useful test files
   would be buried.

3. **`_is_test_path` is deliberately conservative.** It matches `tests/`,
   `test/`, `test_*` directories, and `test_*.py` / `*_test.py` filenames.
   It does NOT match `django/test/` (which is Django's test framework — actual
   source code) because it checks path segments, not substrings. The path
   `django/test/utils.py` has segments `['django', 'test', 'utils.py']` — the
   segment `'test'` DOES match, but this is correct because `django/test/` is
   Django's test client/runner, and a query about "how does the ORM work"
   should deprioritize it. If this causes false positives in other projects,
   the function can be refined later.

4. **Collapsed directories get the penalty too.** When `tests/migrations/`
   collapses 6 test fixture directories into one entry, that entry's priority
   gets the +2 penalty. This prevents collapsed test directories from
   appearing at priority 1 alongside source directories.

5. **Source-preferred seeding reduces Tier 2 fan-out.** The biggest source of
   test-file noise is Tier 2 traversal from test seeds. When `Template` is
   found in both `django/template/base.py` and `tests/template_tests/test_engine.py`,
   the test file seed fans out to `SimpleTestCase`, `override_settings`, and
   other test infrastructure (9,190 edges for `self.assertEqual`!). Preferring
   source seeds keeps traversal within the implementation code.

---

## Expected Outcome

For `hce query "how does the ORM build SQL queries"` on full Django (63K edges):

Before (Phase 7, no test deprioritization):
```
  25 entries total
  Source: 13 entries at priorities 1-3
  Tests:  12 entries at priorities 2-3  ← mixed in with source
```

After (Phase 8):
```
  25 entries total
  Source: 13 entries at priorities 1-3  ← shown first
  Tests:  12 entries at priorities 4-5  ← shown after source
```

For `hce query "how does the migration system detect changes"`:

Before: 123 entries, 105 are tests mixed in at priority 1-2
After:  Source entries at 1-3, test entries at 3-4, clear separation

---

## Tests

Add to `tests/test_dispatch.py`:

```python
from hypergraph_code_explorer.retrieval.dispatch import (
    _is_test_path,
    TEST_PATH_PRIORITY_PENALTY,
)


def test_is_test_path_detects_tests_directory():
    """Files under 'tests/' should be detected as test paths."""
    assert _is_test_path("tests/test_models.py") is True
    assert _is_test_path("tests/migrations/test_auto.py") is True
    assert _is_test_path("project/tests/conftest.py") is True
    assert _is_test_path("django/tests/admin/tests.py") is True


def test_is_test_path_detects_test_prefixed_dirs():
    """Directories starting with 'test_' should be detected."""
    assert _is_test_path("tests/test_migrations_plan/0001.py") is True
    assert _is_test_path("test_something/module.py") is True


def test_is_test_path_detects_test_filenames():
    """Files named test_*.py or *_test.py should be detected."""
    assert _is_test_path("src/test_utils.py") is True
    assert _is_test_path("src/models_test.py") is True
    assert _is_test_path("conftest.py") is True


def test_is_test_path_ignores_source_files():
    """Normal source files should NOT be detected as test paths."""
    assert _is_test_path("django/db/models/query.py") is False
    assert _is_test_path("src/utils.py") is False
    assert _is_test_path("lib/sqlalchemy/orm/session.py") is False
    assert _is_test_path("django/template/base.py") is False


def test_is_test_path_works_on_collapsed_dirs():
    """Collapsed directory paths (ending with /) should also be detected."""
    assert _is_test_path("tests/migrations/") is True
    assert _is_test_path("tests/template_tests/") is True
    assert _is_test_path("django/db/models/") is False
    assert _is_test_path("lib/sqlalchemy/orm/") is False


def test_is_test_path_handles_windows_paths():
    """Backslash paths should work too."""
    assert _is_test_path("tests\\test_models.py") is True
    assert _is_test_path("src\\models.py") is False


def test_dispatch_deprioritizes_test_files():
    """Test files should get higher priority numbers (lower rank) than source files.

    Simulates a codebase where the same symbol appears in both source and test files.
    Source files should always appear before test files in the result.
    """
    builder = HypergraphBuilder()

    # Source file defines QuerySet
    builder.add_edge(HyperedgeRecord(
        edge_id="src_def", relation="defines QuerySet",
        edge_type="DEFINES", sources=["query.QuerySet"], targets=["QuerySet"],
        all_nodes={"query.QuerySet", "QuerySet"},
        source_path="django/db/models/query.py",
    ))
    # Source file imports QuerySet
    builder.add_edge(HyperedgeRecord(
        edge_id="src_imp", relation="imports QuerySet",
        edge_type="IMPORTS", sources=["manager"], targets=["QuerySet"],
        all_nodes={"manager", "QuerySet"},
        source_path="django/db/models/manager.py",
    ))
    # Test file also imports QuerySet
    builder.add_edge(HyperedgeRecord(
        edge_id="test_imp1", relation="imports QuerySet",
        edge_type="IMPORTS", sources=["test_qs"], targets=["QuerySet"],
        all_nodes={"test_qs", "QuerySet"},
        source_path="tests/queries/test_queryset.py",
    ))
    builder.add_edge(HyperedgeRecord(
        edge_id="test_imp2", relation="imports QuerySet",
        edge_type="IMPORTS", sources=["test_man"], targets=["QuerySet"],
        all_nodes={"test_man", "QuerySet"},
        source_path="tests/queries/test_manager.py",
    ))
    # Pad with edges to avoid tiny-graph guards
    for i in range(60):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"pad_{i}", relation="other",
            edge_type="DEFINES", sources=[f"x_{i}"], targets=[f"y_{i}"],
            all_nodes={f"x_{i}", f"y_{i}"},
            source_path=f"django/other/f{i}.py",
        ))

    plan = dispatch("QuerySet", builder)

    # Find source and test entries
    source_files = [f for f in plan.primary_files
                    if not _is_test_path(f.path)]
    test_files = [f for f in plan.primary_files
                  if _is_test_path(f.path)]

    assert len(source_files) > 0, "Should have source file results"
    assert len(test_files) > 0, "Should have test file results"

    # Source files should have lower priority numbers (= higher rank)
    max_source_priority = max(f.priority for f in source_files)
    min_test_priority = min(f.priority for f in test_files)
    assert max_source_priority < min_test_priority, (
        f"Source files (max priority {max_source_priority}) should rank above "
        f"test files (min priority {min_test_priority})"
    )

    # In the sorted list, all source files should appear before all test files
    source_indices = [i for i, f in enumerate(plan.primary_files)
                      if not _is_test_path(f.path)]
    test_indices = [i for i, f in enumerate(plan.primary_files)
                    if _is_test_path(f.path)]
    if source_indices and test_indices:
        assert max(source_indices) < min(test_indices), (
            "All source files should appear before any test files in results"
        )


def test_dispatch_test_penalty_value():
    """The priority penalty for test paths should be exactly TEST_PATH_PRIORITY_PENALTY."""
    builder = HypergraphBuilder()

    # A source file at priority 1
    builder.add_edge(HyperedgeRecord(
        edge_id="src", relation="defines Foo",
        edge_type="DEFINES", sources=["mod.Foo"], targets=["Foo"],
        all_nodes={"mod.Foo", "Foo"},
        source_path="src/models.py",
    ))
    # A test file that would also be priority 1
    builder.add_edge(HyperedgeRecord(
        edge_id="tst", relation="calls Foo",
        edge_type="CALLS", sources=["test_mod.test_foo"], targets=["Foo"],
        all_nodes={"test_mod.test_foo", "Foo"},
        source_path="tests/test_models.py",
    ))
    # Pad
    for i in range(60):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"pad_{i}", relation="other",
            edge_type="DEFINES", sources=[f"x_{i}"], targets=[f"y_{i}"],
            all_nodes={f"x_{i}", f"y_{i}"},
            source_path=f"src/other/f{i}.py",
        ))

    plan = dispatch("Foo", builder)

    test_entries = [f for f in plan.primary_files if _is_test_path(f.path)]
    src_entries = [f for f in plan.primary_files if not _is_test_path(f.path)]

    if test_entries and src_entries:
        # The test entry should be exactly TEST_PATH_PRIORITY_PENALTY higher than
        # what it would have been without the penalty
        for t in test_entries:
            assert t.priority >= 1 + TEST_PATH_PRIORITY_PENALTY, (
                f"Test file priority {t.priority} should be >= "
                f"{1 + TEST_PATH_PRIORITY_PENALTY}"
            )
```

---

## Verification

After implementing:

1. `pytest` — all 163+ existing tests pass, plus the new tests above.

2. Test against full Django:
   ```bash
   hce query "how does the ORM build SQL queries" --cache-dir django/.hce_cache
   ```
   Source files (django/db/...) should appear at priority 1-3.
   Test files (tests/...) should appear at priority 3-5.
   No test files should appear before source files in the output.

3. Test against SQLAlchemy (to verify no regression on codebases with
   test files mixed into lib/):
   ```bash
   hce query "how does Session handle transactions" --cache-dir sqlalchemy/.hce_cache
   ```
   `lib/sqlalchemy/testing/` files should get the penalty.
   `lib/sqlalchemy/orm/` files should not.

4. Test against FastAPI and requests (smaller codebases):
   ```bash
   hce query "how does dependency injection work" --cache-dir fastapi/.hce_cache
   hce lookup Response --cache-dir requests/.hce_cache
   ```
   Should work identically to before if there are no test files in the index.
