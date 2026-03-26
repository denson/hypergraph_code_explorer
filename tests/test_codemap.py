"""Tests for CODEBASE_MAP.md generator."""

from __future__ import annotations

from hypergraph_code_explorer.codemap import generate_codemap
from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord


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
    builder.add_edge(_make_edge("e5", ["auth.HTTPDigestAuth"], ["auth.AuthBase"],
                                edge_type="INHERITS", source_path="auth.py"))
    builder.add_edge(_make_edge("e6", ["adapter.send"], ["urllib3.send"],
                                edge_type="CALLS", source_path="adapters.py"))
    return builder


def test_codemap_contains_all_sections():
    builder = _build_graph()
    md = generate_codemap(builder)
    assert "# Code Map" in md
    assert "## Modules" in md
    assert "## Key Symbols" in md
    assert "## CLI Quick Reference" in md


def test_codemap_contains_modules():
    builder = _build_graph()
    md = generate_codemap(builder)
    assert "sessions.py" in md
    assert "auth.py" in md
    assert "adapters.py" in md


def test_codemap_contains_key_symbols_table():
    builder = _build_graph()
    md = generate_codemap(builder)
    assert "| Symbol | File | Degree |" in md
    assert "|--------|------|--------|" in md


def test_codemap_contains_call_chains():
    builder = _build_graph()
    md = generate_codemap(builder)
    assert "## Call Chains" in md
    # Should have a chain like sessions.Session.send → get_adapter or → adapter.send
    assert "->" in md


def test_codemap_contains_inheritance_trees():
    builder = _build_graph()
    md = generate_codemap(builder)
    assert "## Inheritance Trees" in md
    # AuthBase is the base class
    assert "auth.AuthBase" in md
    assert "<-" in md


def test_codemap_cli_reference():
    builder = _build_graph()
    md = generate_codemap(builder)
    assert "hce lookup" in md
    assert "hce search" in md
    assert "hce query" in md
    assert "--json" in md


def test_codemap_no_line_numbers():
    builder = _build_graph()
    md = generate_codemap(builder)
    # No line numbers should appear (e.g., ":42" or "line 42")
    import re
    # Shouldn't have patterns like ":123" or "line 123" in output
    assert not re.search(r':\d+\b', md), "Line numbers found in codemap output"


def test_codemap_symbol_cap_respected():
    """Add >100 nodes, verify only max_symbols appear in the key symbols table."""
    builder = HypergraphBuilder()
    # Create 150 unique symbols
    for i in range(150):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"e{i}", relation=f"defines symbol_{i}",
            edge_type="DEFINES", sources=[f"module_{i % 10}"],
            targets=[f"symbol_{i}"],
            source_path=f"file_{i % 10}.py",
        ))

    md = generate_codemap(builder, max_symbols=100)
    # Count rows in the key symbols table (lines starting with "| symbol_")
    table_rows = [line for line in md.split("\n")
                  if line.startswith("| ") and not line.startswith("| Symbol") and not line.startswith("|---")]
    assert len(table_rows) <= 100


def test_codemap_call_chain_cap():
    """Verify call chain cap is respected."""
    builder = HypergraphBuilder()
    # Create many call chains
    for i in range(30):
        builder.add_edge(HyperedgeRecord(
            edge_id=f"e{i}", relation=f"calls chain_{i}",
            edge_type="CALLS", sources=[f"caller_{i}"],
            targets=[f"callee_{i}"],
            source_path="calls.py",
        ))

    md = generate_codemap(builder, max_call_chains=5)
    chain_lines = [line for line in md.split("\n")
                   if line.startswith("- ") and "→" in line]
    assert len(chain_lines) <= 5


def test_codemap_save_to_disk(tmp_path):
    builder = _build_graph()
    md = generate_codemap(builder, cache_dir=tmp_path)
    out_file = tmp_path / "CODEBASE_MAP.md"
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == md


def test_codemap_empty_graph():
    builder = HypergraphBuilder()
    md = generate_codemap(builder)
    assert "# Code Map" in md
    # Should still have CLI reference even with no data
    assert "## CLI Quick Reference" in md


def test_codemap_summary_descriptions():
    """SUMMARY edges should be used for module descriptions."""
    builder = HypergraphBuilder()
    builder.add_edge(HyperedgeRecord(
        edge_id="e1", relation="Handles HTTP session management",
        edge_type="SUMMARY", sources=["sessions"], targets=["sessions.Session"],
        source_path="sessions.py",
    ))
    builder.add_edge(HyperedgeRecord(
        edge_id="e2", relation="defines Session",
        edge_type="DEFINES", sources=["sessions"], targets=["sessions.Session"],
        source_path="sessions.py",
    ))

    md = generate_codemap(builder)
    # Should use SUMMARY relation text, not DEFINES-based description
    assert "Handles HTTP session management" in md
