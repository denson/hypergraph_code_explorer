"""Tests for tree-sitter extraction across all supported languages + Python parity."""

from __future__ import annotations

import pytest

from hypergraph_code_explorer.extraction.treesitter_extractor import (
    extract_file,
    is_language_supported,
)
from hypergraph_code_explorer.extraction.code_extractor import CodeHyperedgeExtractor
from hypergraph_code_explorer.ingestion.chunker import Chunk


# ---------------------------------------------------------------------------
# 1. Python extraction parity
# ---------------------------------------------------------------------------

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


def test_python_parity_edge_types():
    """Tree-sitter Python extraction produces the same edge types as the old extractor."""
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    edges = extract_file(_SAMPLE_PYTHON, "sessions.py", "py", [chunk])

    edge_types = {e.edge_type for e in edges}
    assert "IMPORTS" in edge_types
    assert "DEFINES" in edge_types
    assert "CALLS" in edge_types
    assert "RAISES" in edge_types
    assert "SIGNATURE" in edge_types


def test_python_parity_calls():
    """Session.send calls are correctly qualified."""
    chunk = Chunk(
        text=_SAMPLE_PYTHON, chunk_id="test", source_path="sessions.py",
        file_type="py", is_code=True,
    )
    edges = extract_file(_SAMPLE_PYTHON, "sessions.py", "py", [chunk])

    calls = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls) > 0

    # Session.send should be qualified as sessions.Session.send
    send_calls = [e for e in calls if any("sessions.Session.send" in s for s in e.sources)]
    assert len(send_calls) > 0, "Session.send should be qualified as sessions.Session.send"


def test_python_parity_imports():
    """Import edges match old extractor format."""
    chunk = Chunk(
        text=_SAMPLE_PYTHON, chunk_id="test", source_path="sessions.py",
        file_type="py", is_code=True,
    )
    edges = extract_file(_SAMPLE_PYTHON, "sessions.py", "py", [chunk])

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 2  # import os + from pathlib import Path

    for imp in imports:
        assert "sessions" in imp.sources


def test_python_parity_defines():
    """Module-level DEFINES lists all top-level symbols."""
    chunk = Chunk(
        text=_SAMPLE_PYTHON, chunk_id="test", source_path="sessions.py",
        file_type="py", is_code=True,
    )
    edges = extract_file(_SAMPLE_PYTHON, "sessions.py", "py", [chunk])

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    module_defines = [e for e in defines if e.sources == ["sessions"]]
    assert len(module_defines) >= 1

    all_targets = set()
    for e in module_defines:
        all_targets.update(e.targets)
    assert any("Session" in t for t in all_targets)
    assert any("helper" in t for t in all_targets)


def test_python_parity_inherits():
    """INHERITS edges produced correctly."""
    code = "class MyError(ValueError, RuntimeError):\n    pass"
    chunk = Chunk(
        text=code, chunk_id="test", source_path="errors.py",
        file_type="py", is_code=True,
    )
    edges = extract_file(code, "errors.py", "py", [chunk])

    inherits = [e for e in edges if e.edge_type == "INHERITS"]
    assert len(inherits) == 1
    assert "errors.MyError" in inherits[0].sources
    assert "ValueError" in inherits[0].targets
    assert "RuntimeError" in inherits[0].targets


def test_python_parity_raises():
    """RAISES edges produced correctly."""
    chunk = Chunk(
        text=_SAMPLE_PYTHON, chunk_id="test", source_path="sessions.py",
        file_type="py", is_code=True,
    )
    edges = extract_file(_SAMPLE_PYTHON, "sessions.py", "py", [chunk])

    raises = [e for e in edges if e.edge_type == "RAISES"]
    assert len(raises) >= 1
    assert any("ValueError" in e.targets for e in raises)


def test_python_parity_signature():
    """SIGNATURE edges produced for typed functions."""
    chunk = Chunk(
        text=_SAMPLE_PYTHON, chunk_id="test", source_path="sessions.py",
        file_type="py", is_code=True,
    )
    edges = extract_file(_SAMPLE_PYTHON, "sessions.py", "py", [chunk])

    sigs = [e for e in edges if e.edge_type == "SIGNATURE"]
    assert len(sigs) >= 1
    helper_sig = [e for e in sigs if any("helper" in s for s in e.sources)]
    assert len(helper_sig) == 1
    assert "int" in helper_sig[0].targets
    assert "str" in helper_sig[0].targets


# ---------------------------------------------------------------------------
# 2. JavaScript extraction
# ---------------------------------------------------------------------------

_SAMPLE_JS = '''
import { Router } from 'express';
const http = require('http');

class UserService extends BaseService {
    constructor(db) {
        super(db);
        this.db = db;
    }

    async getUser(id) {
        const result = await this.db.query(id);
        if (!result) throw new Error('not found');
        return result;
    }
}

function helper(x) {
    return x + 1;
}
'''


def test_js_defines():
    """JS extraction produces DEFINES edges for classes and functions."""
    chunk = Chunk(
        text=_SAMPLE_JS, chunk_id="test", source_path="service.js",
        file_type="js", is_code=True,
    )
    edges = extract_file(_SAMPLE_JS, "service.js", "js", [chunk])

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) >= 1

    # Module should define UserService and helper
    module_defines = [e for e in defines if e.sources == ["service"]]
    assert len(module_defines) >= 1
    all_targets = set()
    for e in module_defines:
        all_targets.update(e.targets)
    assert any("UserService" in t for t in all_targets)
    assert any("helper" in t for t in all_targets)


def test_js_calls():
    """JS extraction produces CALLS edges."""
    chunk = Chunk(
        text=_SAMPLE_JS, chunk_id="test", source_path="service.js",
        file_type="js", is_code=True,
    )
    edges = extract_file(_SAMPLE_JS, "service.js", "js", [chunk])

    calls = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls) > 0


def test_js_inherits():
    """JS extraction produces INHERITS edge for extends."""
    chunk = Chunk(
        text=_SAMPLE_JS, chunk_id="test", source_path="service.js",
        file_type="js", is_code=True,
    )
    edges = extract_file(_SAMPLE_JS, "service.js", "js", [chunk])

    inherits = [e for e in edges if e.edge_type == "INHERITS"]
    assert len(inherits) >= 1
    assert any("UserService" in e.sources[0] for e in inherits)
    assert any("BaseService" in e.targets for e in inherits)


def test_js_imports():
    """JS extraction produces IMPORTS edges."""
    chunk = Chunk(
        text=_SAMPLE_JS, chunk_id="test", source_path="service.js",
        file_type="js", is_code=True,
    )
    edges = extract_file(_SAMPLE_JS, "service.js", "js", [chunk])

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 1
    all_targets = set()
    for e in imports:
        all_targets.update(e.targets)
    assert any("express" in t for t in all_targets)


def test_js_raises():
    """JS extraction produces RAISES edge for throw."""
    chunk = Chunk(
        text=_SAMPLE_JS, chunk_id="test", source_path="service.js",
        file_type="js", is_code=True,
    )
    edges = extract_file(_SAMPLE_JS, "service.js", "js", [chunk])

    raises = [e for e in edges if e.edge_type == "RAISES"]
    assert len(raises) >= 1
    assert any("Error" in e.targets for e in raises)


# ---------------------------------------------------------------------------
# 3. TypeScript extraction
# ---------------------------------------------------------------------------

_SAMPLE_TS = '''
import { Injectable } from '@angular/core';

interface Serializable {
    serialize(): string;
}

class DataService extends BaseService {
    private cache: Map<string, any>;

    constructor() {
        super();
        this.cache = new Map();
    }

    getData(key: string): any {
        return this.cache.get(key);
    }
}
'''


def test_ts_defines():
    """TS extraction produces DEFINES edges."""
    chunk = Chunk(
        text=_SAMPLE_TS, chunk_id="test", source_path="data.ts",
        file_type="ts", is_code=True,
    )
    edges = extract_file(_SAMPLE_TS, "data.ts", "ts", [chunk])

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) >= 1


def test_ts_imports():
    """TS extraction produces IMPORTS edges."""
    chunk = Chunk(
        text=_SAMPLE_TS, chunk_id="test", source_path="data.ts",
        file_type="ts", is_code=True,
    )
    edges = extract_file(_SAMPLE_TS, "data.ts", "ts", [chunk])

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 1


def test_ts_inherits():
    """TS extraction produces INHERITS for extends."""
    chunk = Chunk(
        text=_SAMPLE_TS, chunk_id="test", source_path="data.ts",
        file_type="ts", is_code=True,
    )
    edges = extract_file(_SAMPLE_TS, "data.ts", "ts", [chunk])

    inherits = [e for e in edges if e.edge_type == "INHERITS"]
    assert len(inherits) >= 1
    assert any("BaseService" in e.targets for e in inherits)


# ---------------------------------------------------------------------------
# 4. Go extraction
# ---------------------------------------------------------------------------

_SAMPLE_GO = '''
package main

import (
    "fmt"
    "net/http"
)

type Server struct {
    Port    int
    Handler http.Handler
}

func (s *Server) Start() error {
    fmt.Println("starting server")
    return http.ListenAndServe(fmt.Sprintf(":%d", s.Port), s.Handler)
}

func NewServer(port int) *Server {
    return &Server{Port: port}
}
'''


def test_go_defines():
    """Go extraction produces DEFINES for struct and functions."""
    chunk = Chunk(
        text=_SAMPLE_GO, chunk_id="test", source_path="server.go",
        file_type="go", is_code=True,
    )
    edges = extract_file(_SAMPLE_GO, "server.go", "go", [chunk])

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) >= 1

    # Module should define Server and NewServer
    module_defines = [e for e in defines if e.sources == ["server"]]
    assert len(module_defines) >= 1


def test_go_calls():
    """Go extraction produces CALLS edges."""
    chunk = Chunk(
        text=_SAMPLE_GO, chunk_id="test", source_path="server.go",
        file_type="go", is_code=True,
    )
    edges = extract_file(_SAMPLE_GO, "server.go", "go", [chunk])

    calls = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls) > 0


def test_go_imports():
    """Go extraction produces IMPORTS edges."""
    chunk = Chunk(
        text=_SAMPLE_GO, chunk_id="test", source_path="server.go",
        file_type="go", is_code=True,
    )
    edges = extract_file(_SAMPLE_GO, "server.go", "go", [chunk])

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 1
    all_targets = set()
    for e in imports:
        all_targets.update(e.targets)
    assert any("fmt" in t for t in all_targets)


# ---------------------------------------------------------------------------
# 5. Rust extraction
# ---------------------------------------------------------------------------

_SAMPLE_RUST = '''
use std::collections::HashMap;
use std::io;

struct Config {
    name: String,
    value: i32,
}

impl Config {
    fn new(name: String) -> Config {
        println!("creating config");
        Config { name, value: 0 }
    }

    fn get_value(&self) -> i32 {
        self.value
    }
}

enum Status {
    Active,
    Inactive,
}

fn process(config: &Config) -> io::Result<()> {
    let map = HashMap::new();
    Ok(())
}
'''


def test_rust_defines():
    """Rust extraction produces DEFINES for struct, enum, impl methods."""
    chunk = Chunk(
        text=_SAMPLE_RUST, chunk_id="test", source_path="config.rs",
        file_type="rs", is_code=True,
    )
    edges = extract_file(_SAMPLE_RUST, "config.rs", "rs", [chunk])

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) >= 1


def test_rust_calls():
    """Rust extraction produces CALLS edges."""
    chunk = Chunk(
        text=_SAMPLE_RUST, chunk_id="test", source_path="config.rs",
        file_type="rs", is_code=True,
    )
    edges = extract_file(_SAMPLE_RUST, "config.rs", "rs", [chunk])

    calls = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls) > 0


def test_rust_imports():
    """Rust extraction produces IMPORTS for use declarations."""
    chunk = Chunk(
        text=_SAMPLE_RUST, chunk_id="test", source_path="config.rs",
        file_type="rs", is_code=True,
    )
    edges = extract_file(_SAMPLE_RUST, "config.rs", "rs", [chunk])

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 1
    all_targets = set()
    for e in imports:
        all_targets.update(e.targets)
    assert any("HashMap" in t or "collections" in t for t in all_targets)


# ---------------------------------------------------------------------------
# 6. Java extraction
# ---------------------------------------------------------------------------

_SAMPLE_JAVA = '''
import java.util.List;
import java.io.IOException;

public class UserRepository extends BaseRepository {
    private final Database db;

    public UserRepository(Database db) {
        super();
        this.db = db;
    }

    public User findById(int id) throws IOException {
        List<User> results = db.query(id);
        if (results.isEmpty()) {
            throw new RuntimeException("not found");
        }
        return results.get(0);
    }
}
'''


def test_java_defines():
    """Java extraction produces DEFINES edges."""
    chunk = Chunk(
        text=_SAMPLE_JAVA, chunk_id="test", source_path="UserRepository.java",
        file_type="java", is_code=True,
    )
    edges = extract_file(_SAMPLE_JAVA, "UserRepository.java", "java", [chunk])

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) >= 1


def test_java_calls():
    """Java extraction produces CALLS edges."""
    chunk = Chunk(
        text=_SAMPLE_JAVA, chunk_id="test", source_path="UserRepository.java",
        file_type="java", is_code=True,
    )
    edges = extract_file(_SAMPLE_JAVA, "UserRepository.java", "java", [chunk])

    calls = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls) > 0


def test_java_inherits():
    """Java extraction produces INHERITS for extends."""
    chunk = Chunk(
        text=_SAMPLE_JAVA, chunk_id="test", source_path="UserRepository.java",
        file_type="java", is_code=True,
    )
    edges = extract_file(_SAMPLE_JAVA, "UserRepository.java", "java", [chunk])

    inherits = [e for e in edges if e.edge_type == "INHERITS"]
    assert len(inherits) >= 1
    assert any("BaseRepository" in e.targets for e in inherits)


def test_java_imports():
    """Java extraction produces IMPORTS edges."""
    chunk = Chunk(
        text=_SAMPLE_JAVA, chunk_id="test", source_path="UserRepository.java",
        file_type="java", is_code=True,
    )
    edges = extract_file(_SAMPLE_JAVA, "UserRepository.java", "java", [chunk])

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 1


def test_java_raises():
    """Java extraction produces RAISES for throw statements."""
    chunk = Chunk(
        text=_SAMPLE_JAVA, chunk_id="test", source_path="UserRepository.java",
        file_type="java", is_code=True,
    )
    edges = extract_file(_SAMPLE_JAVA, "UserRepository.java", "java", [chunk])

    raises = [e for e in edges if e.edge_type == "RAISES"]
    assert len(raises) >= 1
    assert any("RuntimeException" in e.targets for e in raises)


# ---------------------------------------------------------------------------
# 7. Qualified name test
# ---------------------------------------------------------------------------

_TWO_CLASSES_PYTHON = '''
class ClassA:
    def process(self):
        self.run()

class ClassB:
    def process(self):
        self.execute()
'''


def test_qualified_names_python():
    """Two classes with same method name produce qualified names."""
    chunk = Chunk(
        text=_TWO_CLASSES_PYTHON, chunk_id="test", source_path="dual.py",
        file_type="py", is_code=True,
    )
    edges = extract_file(_TWO_CLASSES_PYTHON, "dual.py", "py", [chunk])

    calls = [e for e in edges if e.edge_type == "CALLS"]
    all_sources = [s for e in calls for s in e.sources]

    assert any("ClassA.process" in s for s in all_sources), \
        f"Expected ClassA.process in sources, got {all_sources}"
    assert any("ClassB.process" in s for s in all_sources), \
        f"Expected ClassB.process in sources, got {all_sources}"
    # No unqualified 'process'
    assert not any(s == "dual.process" for s in all_sources), \
        "Should not have unqualified 'dual.process'"


_TWO_CLASSES_JS = '''
class ClassA {
    process() {
        this.run();
    }
}

class ClassB {
    process() {
        this.execute();
    }
}
'''


def test_qualified_names_js():
    """JS: Two classes with same method name produce qualified names."""
    chunk = Chunk(
        text=_TWO_CLASSES_JS, chunk_id="test", source_path="dual.js",
        file_type="js", is_code=True,
    )
    edges = extract_file(_TWO_CLASSES_JS, "dual.js", "js", [chunk])

    calls = [e for e in edges if e.edge_type == "CALLS"]
    all_sources = [s for e in calls for s in e.sources]

    assert any("ClassA.process" in s for s in all_sources), \
        f"Expected ClassA.process in sources, got {all_sources}"
    assert any("ClassB.process" in s for s in all_sources), \
        f"Expected ClassB.process in sources, got {all_sources}"


# ---------------------------------------------------------------------------
# 8. Mixed-language integration test
# ---------------------------------------------------------------------------

_SAMPLE_GO_MINI = '''
package main

import "fmt"

func hello() {
    fmt.Println("hello")
}
'''


def test_mixed_language_integration():
    """extract_all on chunks from Python, JS, and Go produces correct edges."""
    py_chunk = Chunk(
        text="def greet():\n    print('hello')\n",
        chunk_id="py1", source_path="greet.py", file_type="py", is_code=True,
    )
    js_chunk = Chunk(
        text="function greet() { console.log('hello'); }\n",
        chunk_id="js1", source_path="greet.js", file_type="js", is_code=True,
    )
    go_chunk = Chunk(
        text=_SAMPLE_GO_MINI,
        chunk_id="go1", source_path="main.go", file_type="go", is_code=True,
    )

    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract_all([py_chunk, js_chunk, go_chunk])

    # All three languages should produce at least DEFINES edges
    py_edges = [e for e in edges if e.source_path == "greet.py"]
    js_edges = [e for e in edges if e.source_path == "greet.js"]
    go_edges = [e for e in edges if e.source_path == "main.go"]

    assert len(py_edges) > 0, "Python should produce edges"
    assert len(js_edges) > 0, "JavaScript should produce edges"
    assert len(go_edges) > 0, "Go should produce edges"

    # Each should have DEFINES
    assert any(e.edge_type == "DEFINES" for e in py_edges)
    assert any(e.edge_type == "DEFINES" for e in js_edges)
    assert any(e.edge_type == "DEFINES" or e.edge_type == "IMPORTS" for e in go_edges)


# ---------------------------------------------------------------------------
# 9. Unsupported language fallback
# ---------------------------------------------------------------------------

def test_unsupported_language_fallback():
    """Unsupported file type falls back to regex and produces basic edges."""
    chunk = Chunk(
        text="fn hello() {\n    println!(\"hello\");\n}\n",
        chunk_id="zig1", source_path="main.zig", file_type="zig", is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    # Should fall back to regex and produce at least a DEFINES edge
    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) >= 1
    assert any("hello" in e.targets for e in defines)


# ---------------------------------------------------------------------------
# 10. is_language_supported
# ---------------------------------------------------------------------------

def test_language_support():
    """is_language_supported returns correct values."""
    assert is_language_supported("py") is True
    assert is_language_supported("js") is True
    assert is_language_supported("ts") is True
    assert is_language_supported("tsx") is True
    assert is_language_supported("jsx") is True
    assert is_language_supported("go") is True
    assert is_language_supported("rs") is True
    assert is_language_supported("java") is True
    assert is_language_supported("c") is True
    assert is_language_supported("cpp") is True
    assert is_language_supported("h") is True
    assert is_language_supported("hpp") is True
    assert is_language_supported("rb") is True
    assert is_language_supported("php") is True
    assert is_language_supported("zig") is False
    assert is_language_supported("swift") is False
    assert is_language_supported("") is False
