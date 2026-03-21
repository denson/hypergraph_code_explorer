"""Integration tests for the CLI — tests handler functions directly."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord


def _make_edge(edge_id, sources, targets, edge_type="CALLS", source_path="test.py"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path=source_path, chunk_id=f"chunk_{edge_id}",
    )


def _build_and_save(tmp_path) -> Path:
    """Build a small graph and save it as builder.pkl in a cache dir."""
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

    cache_dir = tmp_path / ".hce_cache"
    cache_dir.mkdir()
    builder.save(cache_dir / "builder.pkl")
    return str(cache_dir)


def _capture_output(func, *args, **kwargs) -> str:
    """Capture stdout from a function call."""
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        func(*args, **kwargs)
        return sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout


# --- lookup ---

def test_cli_lookup_text(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_lookup

    args = SimpleNamespace(
        symbol="sessions.Session.send",
        calls=False, callers=False, inherits=False,
        imports=False, raises=False,
        depth=1, json_output=False, cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_lookup, args)
    assert "sessions.Session.send" in output
    assert "sessions.py" in output


def test_cli_lookup_json(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_lookup

    args = SimpleNamespace(
        symbol="sessions.Session.send",
        calls=False, callers=False, inherits=False,
        imports=False, raises=False,
        depth=0, json_output=True, cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_lookup, args)
    data = json.loads(output)
    assert data["query"] == "sessions.Session.send"
    assert len(data["primary_files"]) > 0


def test_cli_lookup_calls_filter(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_lookup

    args = SimpleNamespace(
        symbol="sessions.Session.send",
        calls=True, callers=False, inherits=False,
        imports=False, raises=False,
        depth=1, json_output=True, cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_lookup, args)
    data = json.loads(output)
    # All symbols should be CALLS type
    for sym in data["related_symbols"]:
        if sym["edge_type"]:
            assert sym["edge_type"] == "CALLS"


# --- search ---

def test_cli_search_text(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_search

    args = SimpleNamespace(
        term="auth", type=None, json_output=False,
        cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_search, args)
    assert "auth" in output.lower()


def test_cli_search_json(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_search

    args = SimpleNamespace(
        term="auth", type=None, json_output=True,
        cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_search, args)
    data = json.loads(output)
    assert "query" in data
    assert len(data["related_symbols"]) > 0


# --- query ---

def test_cli_query_text(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_query

    args = SimpleNamespace(
        query="what does Session.send call",
        depth=2, json_output=False,
        cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_query, args)
    assert len(output) > 0


def test_cli_query_json(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_query

    args = SimpleNamespace(
        query="sessions.Session.send",
        depth=2, json_output=True,
        cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_query, args)
    data = json.loads(output)
    assert data["query"] == "sessions.Session.send"


# --- overview ---

def test_cli_overview_text(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_overview

    args = SimpleNamespace(
        top=5, json_output=False,
        cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_overview, args)
    assert "Modules" in output
    assert "Key Symbols" in output


def test_cli_overview_json(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_overview

    args = SimpleNamespace(
        top=5, json_output=True,
        cache_dir=cache_dir, verbose=False,
    )
    output = _capture_output(_run_overview, args)
    data = json.loads(output)
    assert "modules" in data
    assert "key_symbols" in data


# --- stats ---

def test_cli_stats_json(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_stats

    args = SimpleNamespace(
        json_output=True, cache_dir=cache_dir,
    )
    output = _capture_output(_run_stats, args)
    data = json.loads(output)
    assert "num_nodes" in data
    assert "num_edges" in data
    assert "hub_nodes" in data


def test_cli_stats_text(tmp_path):
    cache_dir = _build_and_save(tmp_path)
    from hypergraph_code_explorer.cli import _run_stats

    args = SimpleNamespace(
        json_output=False, cache_dir=cache_dir,
    )
    output = _capture_output(_run_stats, args)
    assert "Graph Statistics" in output
    assert "num_nodes" in output


# --- init ---

def test_cli_lookup_calls_expands_class_to_methods(tmp_path):
    """--calls on a class node should expand to its methods' CALLS edges."""
    builder = HypergraphBuilder()
    # Class node only has DEFINES edges
    builder.add_edge(_make_edge("e1", ["app.MyClass"], ["app.MyClass.do_stuff", "app.MyClass.run"],
                                edge_type="DEFINES", source_path="app.py"))
    # Methods have CALLS edges
    builder.add_edge(_make_edge("e2", ["app.MyClass.do_stuff"], ["helper.process", "helper.validate"],
                                edge_type="CALLS", source_path="app.py"))
    builder.add_edge(_make_edge("e3", ["app.MyClass.run"], ["helper.execute"],
                                edge_type="CALLS", source_path="app.py"))

    cache_dir = tmp_path / ".hce_cache"
    cache_dir.mkdir()
    builder.save(cache_dir / "builder.pkl")

    from hypergraph_code_explorer.cli import _run_lookup

    args = SimpleNamespace(
        symbol="MyClass",
        calls=True, callers=False, inherits=False,
        imports=False, raises=False,
        depth=1, json_output=True, cache_dir=str(cache_dir), verbose=False,
    )
    output = _capture_output(_run_lookup, args)
    data = json.loads(output)

    # Should have found CALLS edges from the methods
    call_symbols = [s for s in data["related_symbols"] if s["edge_type"] == "CALLS"]
    assert len(call_symbols) > 0, "Should find CALLS edges from class methods"
    # Should mention the call targets
    all_targets = []
    for s in call_symbols:
        all_targets.extend(s.get("targets", []))
    assert any("helper" in t for t in all_targets), f"Expected helper targets, got {all_targets}"


def test_cli_init_all(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from hypergraph_code_explorer.cli import _run_init

    args = SimpleNamespace(tool="all", cache_dir=None)
    output = _capture_output(_run_init, args)
    assert "Generated" in output
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".cursorrules").exists()
    assert (tmp_path / "AGENTS.md").exists()
