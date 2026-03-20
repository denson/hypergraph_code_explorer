"""Tests for AST-based code extraction with directed source/target."""

from __future__ import annotations

from hypergraph_code_explorer.extraction.code_extractor import CodeHyperedgeExtractor
from hypergraph_code_explorer.ingestion.chunker import Chunk


_SAMPLE_PYTHON = '''
import os
from pathlib import Path

class Session:
    """HTTP session."""

    def send(self, request, **kwargs):
        adapter = self.get_adapter(url=request.url)
        resp = adapter.send(request, **kwargs)
        return resp

    def get_adapter(self, url):
        return HTTPAdapter()

def helper(x: int) -> str:
    raise ValueError("bad")
'''


def test_extract_python_produces_directed_edges():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    assert len(edges) > 0

    # Check that all edges have sources and targets
    for edge in edges:
        assert len(edge.sources) > 0 or len(edge.targets) > 0
        assert edge.all_nodes == set(edge.sources) | set(edge.targets)


def test_extract_calls_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls_edges) > 0

    # Session.send should call self.get_adapter and adapter.send
    send_calls = [e for e in calls_edges if any("send" in s for s in e.sources)]
    assert len(send_calls) > 0


def test_extract_imports_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 2  # import os, from pathlib import Path

    # Source should be the module
    for imp in imports:
        assert "sessions" in imp.sources


def test_extract_defines_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) > 0

    # Module should define Session and helper
    module_defines = [e for e in defines if "sessions" in e.sources]
    assert len(module_defines) > 0


def test_extract_inherits_edge():
    code = "class MyError(ValueError, RuntimeError):\n    pass"
    chunk = Chunk(
        text=code, chunk_id="test", source_path="errors.py",
        file_type="py", is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    inherits = [e for e in edges if e.edge_type == "INHERITS"]
    assert len(inherits) == 1
    assert "errors.MyError" in inherits[0].sources
    assert "ValueError" in inherits[0].targets
    assert "RuntimeError" in inherits[0].targets


def test_extract_raises_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    raises = [e for e in edges if e.edge_type == "RAISES"]
    assert len(raises) >= 1
    assert any("ValueError" in e.targets for e in raises)


def test_extract_signature_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    sigs = [e for e in edges if e.edge_type == "SIGNATURE"]
    assert len(sigs) >= 1
    # helper(x: int) -> str
    helper_sig = [e for e in sigs if any("helper" in s for s in e.sources)]
    assert len(helper_sig) == 1
    assert "int" in helper_sig[0].targets
    assert "str" in helper_sig[0].targets


# ---------------------------------------------------------------------------
# Tests for per-file extraction (extract_all) — the DEFINES fix
# ---------------------------------------------------------------------------

_MULTI_CLASS_FILE = '''
import os

class Session:
    """HTTP session."""

    def send(self, request):
        return self.adapter.send(request)

    def get(self, url):
        return self.send(url)

class HTTPAdapter:
    """HTTP adapter."""

    def send(self, request):
        return request

    def close(self):
        pass

def standalone_func():
    pass
'''


def test_extract_all_produces_class_defines():
    """extract_all on a multi-class file should produce DEFINES for each class."""
    from hypergraph_code_explorer.ingestion.chunker import chunk_python_source

    chunks = chunk_python_source(_MULTI_CLASS_FILE, "sessions.py")
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract_all(chunks)

    defines = [e for e in edges if e.edge_type == "DEFINES"]

    # Should have: module defines [Session, HTTPAdapter, standalone_func],
    # Session defines [send, get], HTTPAdapter defines [send, close]
    assert len(defines) >= 3, f"Expected >=3 DEFINES edges, got {len(defines)}: {[e.relation for e in defines]}"

    # Class-level DEFINES: sessions.Session should define its methods
    session_defines = [e for e in defines if any("sessions.Session" == s for s in e.sources)]
    assert len(session_defines) >= 1, "Missing Session class-level DEFINES edge"

    # Verify methods are correctly qualified with class name
    for e in session_defines:
        for t in e.targets:
            assert "Session." in t, f"Method target {t} should be qualified with Session"

    # HTTPAdapter should define its methods
    adapter_defines = [e for e in defines if any("sessions.HTTPAdapter" == s for s in e.sources)]
    assert len(adapter_defines) >= 1, "Missing HTTPAdapter class-level DEFINES edge"


def test_extract_all_correct_class_qualification():
    """Methods extracted via extract_all should be qualified as Class.method, not module.method."""
    from hypergraph_code_explorer.ingestion.chunker import chunk_python_source

    chunks = chunk_python_source(_MULTI_CLASS_FILE, "sessions.py")
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract_all(chunks)

    calls = [e for e in edges if e.edge_type == "CALLS"]

    # Session.send should be a source, not sessions.send
    all_sources = [s for e in calls for s in e.sources]
    session_send_sources = [s for s in all_sources if "send" in s and "Session" in s]
    module_send_sources = [s for s in all_sources if s == "sessions.send"]

    assert len(session_send_sources) > 0, "Session.send should appear as a CALLS source"
    # There should be no misqualified 'sessions.send' (without class prefix)
    assert len(module_send_sources) == 0, \
        f"Found misqualified 'sessions.send' sources — methods should be Class.method"


def test_extract_all_module_defines_lists_all_symbols():
    """Module-level DEFINES edge should list ALL top-level symbols, not just one per chunk."""
    from hypergraph_code_explorer.ingestion.chunker import chunk_python_source

    chunks = chunk_python_source(_MULTI_CLASS_FILE, "sessions.py")
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract_all(chunks)

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    module_defines = [e for e in defines if e.sources == ["sessions"]]

    assert len(module_defines) >= 1, "Should have at least one module-level DEFINES edge"

    # The module DEFINES edge should list Session, HTTPAdapter, and standalone_func
    all_targets = set()
    for e in module_defines:
        all_targets.update(e.targets)

    assert any("Session" in t for t in all_targets), "Module DEFINES should include Session"
    assert any("HTTPAdapter" in t for t in all_targets), "Module DEFINES should include HTTPAdapter"
    assert any("standalone_func" in t for t in all_targets), "Module DEFINES should include standalone_func"
