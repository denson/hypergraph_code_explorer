# Replace All Extraction with Tree-Sitter

## Goal

After this change, the extraction layer uses tree-sitter as its single backend for all languages. One code path handles Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, and PHP — producing CALLS, DEFINES, INHERITS, IMPORTS, and RAISES edges uniformly. The Python `ast`-based extractor and the regex-based generic extractor are both retired. All existing Python extraction tests still pass with equivalent output from the tree-sitter backend.

## Context

Real codebases are mixed-language. A Python web app has JS/TS in the frontend, Go services alongside it, Rust libraries, etc. The current extractor has two paths: Python files get full AST-based extraction (CALLS, DEFINES, INHERITS, IMPORTS, RAISES, DECORATES, SIGNATURE), and everything else gets a regex fallback that only produces DEFINES and IMPORTS edges. This means non-Python code is essentially invisible in the graph — you can see that functions exist but not what calls what.

Maintaining two extraction backends adds complexity. Tree-sitter's Python grammar is mature enough that the quality difference vs. the native `ast` module is negligible for our use case (extracting structural relationships, not doing type inference or linting). The simplicity of one backend is worth more than marginal Python fidelity.

PLAN_V3.md design decision #4 originally said "Python AST primary, generic extractor incremental. Keep Python AST as the primary extractor. Don't block v3 on multi-language support." This spec supersedes that decision — tree-sitter is now the unified backend and multi-language support ships as part of this change.

## Scope

### Files Touched

| File | Change |
|------|--------|
| `src/hypergraph_code_explorer/extraction/treesitter_extractor.py` | **NEW.** Single tree-sitter extraction backend for all languages. |
| `src/hypergraph_code_explorer/extraction/code_extractor.py` | **REWRITE internals.** Keep `CodeHyperedgeExtractor` class and its public methods (`extract()`, `extract_all()`). Replace the internal dispatch to route all languages through tree-sitter. Keep a minimal regex fallback for truly unsupported languages. |
| `src/hypergraph_code_explorer/extraction/_legacy_python_extractor.py` | **NEW.** Move the old `_PythonHyperedgeVisitor` and related methods here as a reference. Do not import this anywhere — it exists only for comparison during validation. |
| `pyproject.toml` | Add tree-sitter + language grammar packages to `dependencies`. Update project description to say "multi-language". |
| `tests/test_treesitter_extractor.py` | **NEW.** Tests for tree-sitter extraction across all supported languages + Python parity test. |
| `tests/test_code_extractor.py` | **MODIFY if needed.** Update test harness if class internals changed, but all assertions about edge types and node names must still pass. |
| `PLAN_V3.md` | Update design decision #4 to reflect tree-sitter as the unified backend. |
| `README.md` | Update description from "Python" to "multi-language" if this file exists. |

### Files NOT Touched

These files and modules must remain completely unchanged:

| File / Module | Why it's off-limits |
|------|-----|
| `src/hypergraph_code_explorer/models.py` | Core data models (`EdgeType`, `HyperedgeRecord`). Changing these would break the builder, retrieval, CLI, and all tests. |
| `src/hypergraph_code_explorer/ingestion/chunker.py` | `Chunk` dataclass and chunking logic. The extraction layer consumes chunks — it doesn't define them. |
| `src/hypergraph_code_explorer/ingestion/converter.py` | File discovery and conversion. Not related to extraction. |
| `src/hypergraph_code_explorer/graph/builder.py` | Hypergraph construction. Consumes `HyperedgeRecord` — doesn't care how they were produced. |
| `src/hypergraph_code_explorer/graph/simplify.py` | Node deduplication. Downstream of extraction. |
| `src/hypergraph_code_explorer/graph/summaries.py` | LLM-based summaries. Unrelated. |
| `src/hypergraph_code_explorer/graph/embeddings.py` | Semantic search vectors. Unrelated. |
| `src/hypergraph_code_explorer/retrieval/*` | All retrieval modules (dispatch, lookup, traverse, textsearch, semantic, plan). Downstream of extraction. |
| `src/hypergraph_code_explorer/cli.py` | CLI interface. Consumes the extraction pipeline, doesn't define it. |
| `src/hypergraph_code_explorer/mcp_server.py` | MCP protocol server. Unrelated. |
| `src/hypergraph_code_explorer/pipeline.py` | Orchestration. Calls `CodeHyperedgeExtractor` — as long as the public interface is preserved, pipeline doesn't need changes. |
| `src/hypergraph_code_explorer/codemap.py` | CODEBASE_MAP.md generator. Downstream. |

## Implementation Plan

### Step 1: Add tree-sitter as a required dependency

In `pyproject.toml`, add tree-sitter to the main `dependencies` list (not optional):

```toml
dependencies = [
    "anthropic>=0.46.0",
    "python-dotenv>=1.0.0",
    "markitdown[all]>=0.1.0",
    "tree-sitter>=0.24.0",
]
```

Also add the language grammar packages. Check PyPI for current package names — the naming convention may have changed. They may be individual packages (`tree-sitter-python`, `tree-sitter-javascript`, etc.) or a single bundle (`tree-sitter-languages`). Use whichever approach gives us these languages with the least dependency bloat:

- Python, JavaScript, TypeScript (including TSX/JSX), Go, Rust, Java, C, C++, Ruby, PHP

If individual packages: add them all to `dependencies`.
If bundled: add the single bundle package.

Update the project description to say "multi-language" instead of "Python".

**Verify:** `pip install -e .` succeeds and `python -c "import tree_sitter"` works.

### Step 2: Create the tree-sitter extractor module

Create: `src/hypergraph_code_explorer/extraction/treesitter_extractor.py`

This is the new single extraction backend. It must:

1. **Map file_type to tree-sitter language**:
   ```python
   LANGUAGE_MAP = {
       "py": "python",
       "js": "javascript",
       "ts": "typescript",
       "tsx": "tsx",
       "jsx": "javascript",
       "go": "go",
       "rs": "rust",
       "java": "java",
       "c": "c",
       "cpp": "cpp",
       "h": "c",
       "hpp": "cpp",
       "rb": "ruby",
       "php": "php",
   }
   ```

2. **Lazy-load parsers**: Create tree-sitter `Parser` objects on first use and cache them by language name. Each parser needs the corresponding `Language` object from the grammar package.

3. **Define tree-sitter queries per language** for extracting each edge type. This is the bulk of the work. Each language has different grammar node types:

   **Python**: `function_definition`, `class_definition`, `call`, `import_statement`, `import_from_statement`, `raise_statement`, `decorator`

   **JavaScript/TypeScript**: `function_declaration`, `arrow_function`, `class_declaration`, `call_expression`, `import_statement`, `throw_statement`, `method_definition`

   **Go**: `function_declaration`, `method_declaration`, `type_declaration` (struct), `call_expression`, `import_declaration`

   **Rust**: `function_item`, `impl_item`, `struct_item`, `enum_item`, `call_expression`, `use_declaration`, `macro_invocation`

   **Java**: `class_declaration`, `method_declaration`, `constructor_declaration`, `method_invocation`, `import_declaration`, `throw_statement`, `interface_declaration`

   **C/C++**: `function_definition`, `struct_specifier`, `class_specifier`, `call_expression`, `preproc_include`

   **Ruby**: `method`, `class`, `module`, `call`, `method_call`

   **PHP**: `function_definition`, `method_declaration`, `class_declaration`, `function_call_expression`, `use_declaration`

   Use tree-sitter S-expression queries where possible. For example, for Python calls:
   ```scheme
   (call function: (attribute object: (_) @obj attribute: (identifier) @method))
   (call function: (identifier) @func)
   ```

   Fall back to tree walking when S-expression queries aren't expressive enough.

4. **Qualify names by walking up the tree**. When you find a method definition, walk up to find the enclosing class/struct/impl. Produce `ClassName.method_name` not just `method_name`. This is critical for graph quality — without qualified names, you get collisions between methods of different classes.

5. **Track the enclosing function/method for CALLS edges**. When you find a `call_expression`, the source of the CALLS edge is the enclosing function (qualified), and the target is the called function. Walk up the tree to find the enclosing function context.

6. **Return `list[HyperedgeRecord]`** — same dataclass as before. Use the same `_make_edge_id` pattern (md5 hash of edge_type + key + source_path + counter). Keep `source_path`, `chunk_id`, and `chunk_text` populated.

7. **Export two public functions**:
   ```python
   def extract_file(source_code: str, source_path: str, file_type: str,
                    chunks: list[Chunk]) -> list[HyperedgeRecord]:
       """Extract edges from a full source file using tree-sitter.
       Associates edges back to chunks by line range.
       Raises ValueError if the language is not supported."""

   def is_language_supported(file_type: str) -> bool:
       """Check if tree-sitter supports this file type."""
   ```

**Verify:** Import the module, call `is_language_supported("py")` returns True, `is_language_supported("zig")` returns False.

### Step 3: Rewrite code_extractor.py

Replace the internals of `CodeHyperedgeExtractor` while keeping the public interface (`extract()`, `extract_all()`):

```python
class CodeHyperedgeExtractor:
    """Extracts code hyperedges using tree-sitter for all supported languages."""

    def extract(self, chunk: Chunk) -> list[HyperedgeRecord]:
        """Extract from a single chunk."""
        if not chunk.is_code:
            return []
        return self._extract_file_chunks(chunk.source_path, [chunk])

    def extract_all(self, chunks: list[Chunk]) -> list[HyperedgeRecord]:
        """Extract from all code chunks. Groups by file for full-file parsing."""
        edges: list[HyperedgeRecord] = []

        # Group all code chunks by source file
        chunks_by_file: dict[str, list[Chunk]] = defaultdict(list)
        for chunk in chunks:
            if chunk.is_code:
                chunks_by_file[chunk.source_path].append(chunk)

        # Extract each file
        for source_path, file_chunks in chunks_by_file.items():
            edges.extend(self._extract_file_chunks(source_path, file_chunks))

        return edges

    def _extract_file_chunks(self, source_path: str, chunks: list[Chunk]) -> list[HyperedgeRecord]:
        """Extract from a single file's chunks using tree-sitter."""
        file_type = chunks[0].file_type

        if not is_language_supported(file_type):
            return self._extract_regex_fallback(chunks)

        # Get full file source from the largest chunk
        full_source = max(chunks, key=lambda c: len(c.text)).text

        edges = extract_file(full_source, source_path, file_type, chunks)
        return edges

    def _extract_regex_fallback(self, chunks: list[Chunk]) -> list[HyperedgeRecord]:
        """Minimal regex fallback for languages tree-sitter doesn't support."""
        # Keep the existing regex logic from _extract_generic_code() here
        # as a last resort for truly unsupported file types
        ...
```

The key change: no more branching on `file_type == "py"`. All languages go through the same path. The only branching is `is_language_supported()` → tree-sitter vs regex fallback for truly unknown languages.

**Verify:** `from hypergraph_code_explorer.extraction.code_extractor import CodeHyperedgeExtractor` still works.

### Step 4: Preserve the old Python extractor as reference

Move the old `_PythonHyperedgeVisitor` and its helper methods (`_extract_python_file`, `_extract_python_chunk`) to:

```
src/hypergraph_code_explorer/extraction/_legacy_python_extractor.py
```

Do not import this file anywhere. It exists only as a reference for what the Python tree-sitter queries need to match. It can be deleted later once tree-sitter Python extraction is validated.

### Step 5: Write tests

Create `tests/test_treesitter_extractor.py` with:

1. **Python extraction parity**: Take the existing Python test fixtures from `test_code_extractor.py` and run them through the new tree-sitter extractor. Verify that the same edge types are produced with the same qualified names. This is the critical test — if Python extraction regresses, the rewrite is broken.

2. **JavaScript extraction**: A small JS file with a class extending another, method calls, imports, and a throw. Verify DEFINES, CALLS, INHERITS, IMPORTS, RAISES edges.

3. **TypeScript extraction**: A TS file with an interface, a class implementing it, generics, and imports. Verify edges.

4. **Go extraction**: A Go file with a struct, methods with receivers, function calls, and imports. Verify edges.

5. **Rust extraction**: A Rust file with a struct, impl block, trait implementation, function calls, and use statements. Verify edges.

6. **Java extraction**: A Java file with a class extending another, interface implementation, method calls, imports, and throws. Verify edges.

7. **Qualified name test**: A file (any OOP language) with two classes, each with a method called `process()`. Verify that the extractor produces `ClassA.process` and `ClassB.process`, not two unqualified `process` nodes.

8. **Mixed-language integration test**: Run `extract_all()` on chunks from Python, JS, and Go files together. Verify all get tree-sitter extraction and produce correct edges.

9. **Unsupported language fallback**: Pass a chunk with `file_type="zig"` (not in LANGUAGE_MAP), verify it falls back to regex and produces basic DEFINES + IMPORTS edges.

10. **Existing test suite**: All tests in `test_code_extractor.py` MUST STILL PASS. Update the test harness if the class internals changed (e.g., private method names), but the assertions about edge types and node names should hold.

**Verify:** `pytest tests/test_treesitter_extractor.py -v` passes. `pytest tests/test_code_extractor.py -v` passes. `pytest tests/ -v` (full suite) passes.

### Step 6: Update documentation

1. Update `PLAN_V3.md` design decision #4 — change from "Python AST primary, generic extractor incremental" to: "Tree-sitter unified backend. All languages go through tree-sitter for extraction. The original Python AST extractor is preserved in `_legacy_python_extractor.py` for reference."

2. In `README.md` (if it exists), update the description from "Python" to "multi-language".

3. Update `pyproject.toml` project description.

4. Update any comments in extraction files that reference "Python only" or "Python AST".

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `tree-sitter` | >=0.24.0 | Core tree-sitter Python bindings |
| Language grammars | Latest | Python, JS, TS, Go, Rust, Java, C, C++, Ruby, PHP |

Check PyPI for current grammar package names before adding. The ecosystem may use individual packages (`tree-sitter-python`, etc.) or a single bundle (`tree-sitter-languages`). Use whichever approach works with least bloat.

## Quality Bar

- All existing tests in `test_code_extractor.py` pass
- Python extraction produces the same edge types and qualified names as the old `ast`-based extractor
- Every supported language produces at minimum DEFINES and CALLS edges
- INHERITS and IMPORTS edges are produced where the language supports them
- Qualified names (`ClassName.method`) work for all OOP languages (Python, JS, TS, Java, Ruby, PHP, C++, Rust impl blocks)
- The regex fallback only activates for languages not in LANGUAGE_MAP
- `pytest tests/ -v` passes with zero failures
- `hce index` on a Python codebase produces the same graph as before (run against the repo itself as a smoke test: `hce index ./src/hypergraph_code_explorer --skip-summaries`)

## What NOT to Do

- Do not change `HyperedgeRecord`, `EdgeType`, or `Chunk` dataclasses
- Do not change the builder, retrieval, or CLI layers — this change is extraction only
- Do not add new edge types — use the existing `EdgeType` enum
- Do not delete the old Python extractor — move it to `_legacy_python_extractor.py`
- Do not skip the Python parity test — it's the most important test in the suite
- Do not make tree-sitter optional — it is a required core dependency
- Do not change `pipeline.py` — it calls `CodeHyperedgeExtractor` whose public interface is preserved

## Cross-Repo Context

The `hce-visualize` skill (sibling directory `../hce-visualize/`) depends on the extraction output format — specifically the `builder.pkl` file that the graph builder produces from `HyperedgeRecord` objects. Since `HyperedgeRecord` and the builder are not changing, the skill will continue to work without modification. However, the skill's `references/quickstart.md` currently says "Python only" in its Limitations section — that will need a separate update after this change lands.

The three test codebases (Django, FastAPI, requests) have existing `.hce_cache/builder.pkl` files built with the old Python extractor. After this change, re-indexing them should produce equivalent graphs. Use `hce stats` to compare node/edge counts before and after.

## Prior Decisions

- **PLAN_V3.md, Design Decision #4**: Originally deferred multi-language support. This spec supersedes that decision based on the practical need for mixed-language codebase support.
- **Cowork conversation (2026-03-22)**: Discussed keeping Python AST as a special case vs. going all-in on tree-sitter. Decision: tree-sitter for everything. The quality delta for Python is negligible, and one code path is simpler than two.
- **`extraction/code_extractor.py` current architecture**: The Python path uses per-file grouping (parse full file source, associate edges to chunks by line range). The tree-sitter backend must follow the same pattern for all languages.
