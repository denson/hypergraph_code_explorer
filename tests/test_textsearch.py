"""Tests for Tier 3 — Text Search."""

from __future__ import annotations

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.retrieval.textsearch import (
    get_matched_nodes,
    text_search,
    _extract_search_terms,
)


def _make_edge(edge_id, sources, targets, edge_type="CALLS", source_path="test.py"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path=source_path, chunk_id=f"chunk_{edge_id}",
    )


def _build_graph() -> HypergraphBuilder:
    builder = HypergraphBuilder()
    builder.add_edge(_make_edge("e1", ["auth.AuthBase"], ["auth.HTTPBasicAuth"],
                                edge_type="DEFINES", source_path="auth.py"))
    builder.add_edge(_make_edge("e2", ["auth.AuthBase"], ["auth.HTTPDigestAuth"],
                                edge_type="DEFINES", source_path="auth.py"))
    builder.add_edge(_make_edge("e3", ["sessions.Session"], ["sessions.Session.send"],
                                edge_type="DEFINES", source_path="sessions.py"))
    builder.add_edge(_make_edge("e4", ["utils.is_valid_cidr"], ["int", "socket.inet_aton"],
                                edge_type="CALLS", source_path="utils.py"))
    builder.add_edge(_make_edge("e5", ["adapters.HTTPAdapter"], ["urllib3.send"],
                                edge_type="CALLS", source_path="adapters.py"))
    return builder


def test_extract_search_terms_filters_stopwords():
    terms = _extract_search_terms("how does authentication work")
    assert "how" not in terms
    assert "does" not in terms
    assert "work" not in terms
    assert "authentication" in terms


def test_extract_search_terms_splits_dots():
    terms = _extract_search_terms("Session.send")
    assert "session" in terms
    assert "send" in terms


def test_text_search_substring_match():
    builder = _build_graph()
    plan = text_search("auth", builder)
    assert not plan.is_empty()
    matched_names = {s.name for s in plan.related_symbols}
    # Should find auth-related nodes
    assert any("auth" in n.lower() for n in matched_names)


def test_text_search_finds_multiple_matches():
    builder = _build_graph()
    plan = text_search("auth", builder)
    matched_names = {s.name for s in plan.related_symbols}
    # Should find AuthBase, HTTPBasicAuth, HTTPDigestAuth
    assert len(matched_names) >= 2


def test_text_search_returns_files():
    builder = _build_graph()
    plan = text_search("auth", builder)
    paths = {f.path for f in plan.primary_files}
    assert "auth.py" in paths


def test_text_search_returns_grep_suggestions():
    builder = _build_graph()
    plan = text_search("auth", builder)
    patterns = {g.pattern for g in plan.grep_suggestions}
    assert "auth" in patterns


def test_text_search_no_match():
    builder = _build_graph()
    plan = text_search("zzz_nonexistent_zzz", builder)
    assert plan.is_empty()


def test_text_search_exact_stem_ranks_higher():
    builder = _build_graph()
    nodes = get_matched_nodes("send", builder)
    # "sessions.Session.send" has stem "send" -> exact match
    assert any("send" in n.lower() for n in nodes)


def test_text_search_file_path_match():
    builder = _build_graph()
    plan = text_search("adapters", builder)
    # Should find the adapters.py file or HTTPAdapter node
    assert not plan.is_empty()


def test_get_matched_nodes_returns_list():
    builder = _build_graph()
    nodes = get_matched_nodes("auth", builder)
    assert isinstance(nodes, list)
    assert len(nodes) > 0
    assert all(isinstance(n, str) for n in nodes)


def test_get_matched_nodes_respects_max_results():
    builder = _build_graph()
    nodes = get_matched_nodes("auth", builder, max_results=1)
    assert len(nodes) <= 1


def test_text_search_collapses_directory():
    """When 3+ files from the same directory match, collapse into one entry."""
    builder = HypergraphBuilder()
    # Create 5 files in the same directory, all matching "migration"
    for i in range(5):
        builder.add_edge(_make_edge(
            f"mig_{i}",
            [f"migration_{i}.Migration"],
            [f"migration_{i}.Migration.dependencies"],
            edge_type="DEFINES",
            source_path=f"django/db/migrations/000{i}_initial.py",
        ))
    # Also add a file from a different directory
    builder.add_edge(_make_edge(
        "other", ["migration_utils.MigrationLoader"],
        ["migration_utils.MigrationLoader.load"],
        edge_type="DEFINES",
        source_path="django/db/migration_utils.py",
    ))

    plan = text_search("migration", builder)
    assert not plan.is_empty()

    # Should have collapsed the 5 django/db/migrations/ files into one entry
    dir_entries = [f for f in plan.primary_files if f.path.endswith("/")]
    individual_entries = [f for f in plan.primary_files if not f.path.endswith("/")]

    assert len(dir_entries) >= 1, "Should have at least one collapsed directory entry"
    # The collapsed entry should mention "5 files match"
    migration_dir = [d for d in dir_entries if "migrations" in d.path]
    assert len(migration_dir) == 1
    assert "5 files" in migration_dir[0].reason

    # The individual file from the other directory should NOT be collapsed
    other_files = [f for f in individual_entries if "migration_utils" in f.path]
    assert len(other_files) == 1


def test_text_search_no_collapse_under_threshold():
    """Files from the same directory should NOT collapse when < 3."""
    builder = HypergraphBuilder()
    for i in range(2):
        builder.add_edge(_make_edge(
            f"e_{i}", [f"mod_{i}.Foo"], [f"mod_{i}.Foo.bar"],
            edge_type="DEFINES",
            source_path=f"pkg/mod_{i}.py",
        ))

    plan = text_search("foo", builder)
    # With only 2 files in pkg/, no collapsing should happen
    dir_entries = [f for f in plan.primary_files if f.path.endswith("/")]
    assert len(dir_entries) == 0, "Should not collapse when < 3 files in directory"


def test_directory_collapse_ranking_name_bonus():
    """Directories whose name matches the search term should rank higher."""
    builder = HypergraphBuilder()
    # Create nodes in a directory whose name matches 'template'
    for i in range(5):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"tpl_{i}", relation=f"template thing {i}",
            edge_type="DEFINES", sources=[f"TemplateClass{i}"], targets=[f"method{i}"],
            source_path=f"/project/template/file{i}.py",
        ))
    # Create nodes in a directory that merely uses templates
    for i in range(5):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"view_{i}", relation=f"imports template {i}",
            edge_type="IMPORTS", sources=[f"view{i}"], targets=[f"TemplateClass{i}"],
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
            source_path=f"/project/auth/migrations/m{i}.py",
        ))
    # Directory with 3 matching files
    for i in range(3):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"small_{i}", relation=f"auth thing {i}",
            edge_type="DEFINES", sources=[f"SmallAuth{i}"], targets=[f"func{i}"],
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
