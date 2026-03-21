"""Tests for the query dispatcher."""

from __future__ import annotations

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.dispatch import (
    classify_query,
    dispatch,
    _collapse_directories,
    _extract_dispatch_terms,
    _is_test_path,
    TEST_PATH_PRIORITY_PENALTY,
)
from hypergraph_code_explorer.retrieval.plan import FileSuggestion


def _make_edge(edge_id, sources, targets, edge_type="CALLS", source_path="test.py"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path=source_path, chunk_id=f"chunk_{edge_id}",
    )


def _build_graph() -> HypergraphBuilder:
    builder = HypergraphBuilder()
    builder.add_edge(_make_edge("e1", ["sessions.Session"], ["sessions.Session.send", "sessions.Session.get"],
                                edge_type="DEFINES", source_path="sessions.py"))
    builder.add_edge(_make_edge("e2", ["sessions.Session.send"], ["get_adapter", "adapter.send"],
                                edge_type="CALLS", source_path="sessions.py"))
    builder.add_edge(_make_edge("e3", ["auth.AuthBase"], ["auth.HTTPBasicAuth", "auth.HTTPDigestAuth"],
                                edge_type="DEFINES", source_path="auth.py"))
    builder.add_edge(_make_edge("e4", ["auth.HTTPBasicAuth"], ["auth.AuthBase"],
                                edge_type="INHERITS", source_path="auth.py"))
    builder.add_edge(_make_edge("e5", ["sessions"], ["HTTPAdapter"],
                                edge_type="IMPORTS", source_path="sessions.py"))
    return builder


# --- classify_query tests ---

def test_classify_identifier():
    builder = _build_graph()
    classes = classify_query("sessions.Session.send", builder)
    assert "identifier" in classes


def test_classify_structural():
    builder = _build_graph()
    classes = classify_query("what does Session call", builder)
    assert "structural" in classes


def test_classify_text_search():
    builder = _build_graph()
    classes = classify_query("authentication mechanism", builder)
    assert "text_search" in classes


def test_classify_broad():
    builder = _build_graph()
    classes = classify_query("explain the architecture of this project", builder)
    assert "broad" in classes


def test_classify_no_match_defaults_to_text():
    builder = _build_graph()
    classes = classify_query("zzz_totally_unknown_zzz", builder)
    assert "text_search" in classes


# --- dispatch tests ---

def test_dispatch_identifier_returns_results():
    builder = _build_graph()
    plan = dispatch("sessions.Session.send", builder)
    assert not plan.is_empty()
    assert 1 in plan.tiers_used


def test_dispatch_structural_uses_tier_2():
    builder = _build_graph()
    plan = dispatch("what does sessions.Session.send call", builder)
    assert 2 in plan.tiers_used
    # Should find call targets
    all_targets = []
    for sym in plan.related_symbols:
        if sym.edge_type == "CALLS":
            all_targets.extend(sym.targets)
    assert "get_adapter" in all_targets or "adapter.send" in all_targets


def test_dispatch_text_search_fallback():
    builder = _build_graph()
    plan = dispatch("AuthBase", builder)
    # "AuthBase" exactly matches a node, but also test text fallback for partial match
    assert not plan.is_empty()
    matched_names = {s.name for s in plan.related_symbols}
    assert any("auth" in n.lower() for n in matched_names)


def test_text_search_feeds_into_structural_expansion():
    """When Tier 1 finds nothing but Tier 3 finds substring matches,
    those matches should be expanded structurally via Tier 1+2."""
    builder = HypergraphBuilder()
    # "retry" is a substring of "RetryHandler" and "retry_logic"
    builder.add_edge(HyperedgeRecord(
        edge_id="e1", relation="module defines RetryHandler",
        edge_type="DEFINES", sources=["network"], targets=["network.RetryHandler"],
        source_path="network.py",
    ))
    builder.add_edge(HyperedgeRecord(
        edge_id="e2", relation="RetryHandler calls backoff",
        edge_type="CALLS", sources=["network.RetryHandler"], targets=["backoff"],
        source_path="network.py",
    ))

    plan = dispatch("retry logic", builder)

    # Should have found retry-related nodes via text search
    # AND expanded them structurally (Tier 1+2 feedback)
    assert len(plan.primary_files) > 0
    assert any("network" in f.path.lower() for f in plan.primary_files)
    # Should have tier 3 (text search) in tiers_used
    assert 3 in plan.tiers_used


def test_dispatch_returns_files():
    builder = _build_graph()
    plan = dispatch("sessions.Session", builder)
    paths = {f.path for f in plan.primary_files}
    assert "sessions.py" in paths


def test_dispatch_returns_grep_suggestions():
    builder = _build_graph()
    plan = dispatch("sessions.Session.send", builder)
    assert len(plan.grep_suggestions) > 0


def test_dispatch_with_edge_type_override():
    builder = _build_graph()
    plan = dispatch("sessions.Session", builder, edge_types=["CALLS"])
    # All symbols should be CALLS type (filter applied)
    for sym in plan.related_symbols:
        if sym.edge_type:  # text_search symbols may have empty edge_type
            assert sym.edge_type == "CALLS"


def test_dispatch_empty_query():
    builder = _build_graph()
    plan = dispatch("", builder)
    # Should not crash, may return empty
    assert isinstance(plan.query, str)


def test_dispatch_depth_controls_traversal():
    builder = _build_graph()
    plan_shallow = dispatch("sessions.Session.send", builder, depth=1)
    plan_deep = dispatch("sessions.Session.send", builder, depth=3)
    # Deeper traversal should find at least as many symbols
    assert len(plan_deep.related_symbols) >= len(plan_shallow.related_symbols)


def test_dispatch_nl_query_filters_low_specificity_seeds():
    """NL queries should not traverse from generic high-degree nodes."""
    builder = HypergraphBuilder()
    # 'sql' appears in 100 out of 103 edges (~97%) — very generic
    for i in range(100):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"imp_{i}", relation=f"imports sql",
            edge_type="IMPORTS", sources=[f"mod_{i}"], targets=["sql"],
            source_path=f"mod_{i}.py",
        ))
    # 'QuerySet' is specific — only 3 out of 103 edges (~3%)
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"qs_{i}", relation="defines QuerySet",
            edge_type="DEFINES", sources=["query"], targets=["QuerySet"],
            source_path="query.py",
        ))

    plan = dispatch("how does QuerySet use sql", builder)

    # Should NOT have 100+ files from traversing every sql importer
    assert len(plan.primary_files) < 30, (
        f"Expected <30 files but got {len(plan.primary_files)} — "
        f"low-specificity 'sql' node is probably being traversed"
    )


def test_dispatch_specificity_works_at_small_scale():
    """Specificity filtering should work on small graphs too."""
    builder = HypergraphBuilder()
    # 'os' touches 15 of 30 edges (50%) — generic even in a small graph
    for i in range(15):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"os_{i}", relation="imports os",
            edge_type="IMPORTS", sources=[f"mod_{i}"], targets=["os"],
            source_path=f"mod_{i}.py",
        ))
    # 'Parser' touches 3 of 30 edges (10%) — specific
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"p_{i}", relation="defines Parser",
            edge_type="DEFINES", sources=["parse"], targets=["Parser"],
            source_path="parse.py",
        ))
    # Fill in some other edges
    for i in range(12):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"other_{i}", relation="other",
            edge_type="CALLS", sources=[f"x_{i}"], targets=[f"y_{i}"],
            source_path=f"x_{i}.py",
        ))

    plan = dispatch("how does Parser use os", builder)

    # Should focus on Parser, not explode through all 15 os-importing files
    assert len(plan.primary_files) < 20, (
        f"Expected <20 files but got {len(plan.primary_files)} — "
        f"small-graph specificity filtering may not be working"
    )


# --- Directory collapse tests ---

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
        *[FileSuggestion(path=f"/proj/sql/f{i}.py", symbols=[f"Sym{i}"], reason="t", priority=1)
          for i in range(5)],
        # 5 files in utils/ directory — name does NOT match
        *[FileSuggestion(path=f"/proj/utils/f{i}.py", symbols=[f"Util{i}"], reason="t", priority=1)
          for i in range(5)],
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

    # Pad with unrelated edges — keep total under 50 so specificity
    # filtering is disabled (MIN_EDGES_FOR_SPECIFICITY=50) and the test
    # focuses purely on directory collapse behaviour.
    for i in range(20):
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


# --- Test-path deprioritization tests ---

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
    # Keep total edges under 50 so specificity filtering is disabled
    # (MIN_EDGES_FOR_SPECIFICITY=50). This test focuses on test-path
    # deprioritization, not specificity.
    for i in range(40):
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
    # Keep under 50 edges to disable specificity filtering
    for i in range(40):
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
