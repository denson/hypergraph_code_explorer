# HCE Quickstart

## Install

HCE requires Python 3.11+. This skill lives inside the HCE repo at `skill/`, so install from the repo root:

```bash
# Navigate to the repo root (one level up from skill/)
cd "$(dirname "<skill-dir>")"

# Install with pip (editable mode so `hce` CLI lands on PATH)
pip install -e .

# Verify
hce --help
```

If you're in a Cowork or sandboxed environment, add `--break-system-packages`:
```bash
pip install -e . --break-system-packages
```

If the repo isn't cloned yet:
```bash
git clone https://github.com/denson/hypergraph_code_explorer.git
cd hypergraph_code_explorer
pip install -e .
```

Optional extras:
```bash
pip install -e ".[embed]"   # Tier 4 semantic search (sentence-transformers)
pip install -e ".[server]"  # MCP server mode
pip install -e ".[all]"     # Everything
```

## Index a codebase

Point `hce index` at the **source root** — the directory containing the actual source code, not the repo root.

```bash
hce index ./my-project/src --skip-summaries
```

HCE uses tree-sitter for multi-language extraction. Supported languages: Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, PHP. Mixed-language projects are fully supported — each file is parsed with the appropriate language grammar.

Finding the source root:
- Python: `django/django/` (check `pyproject.toml` for package location)
- Node.js: `my-app/src/` (check `package.json` → `main` or `module`)
- Go: the directory with `go.mod`, or the package directory
- Rust: `my-crate/src/`
- Java: `my-app/src/main/java/`

The `--skip-summaries` flag keeps the pipeline zero-cost — no API key needed, no LLM calls. The index saves to `.hce_cache/` inside the source root.

For large codebases, add `--verbose` to see progress:
```bash
hce index ./django/django --skip-summaries --verbose
```

## Check the index

```bash
hce stats --cache-dir ./fastapi/fastapi/.hce_cache
```

Scale reference:

| Codebase | Files | Nodes | Edges | Index Time |
|----------|-------|-------|-------|------------|
| requests | 18 | 906 | 485 | ~3s |
| FastAPI | 48 | 1,264 | 1,214 | ~9s |
| Django | 1,163 | 23,614 | 19,382 | ~196s |

## Query the graph

### Exact symbol lookup
```bash
hce lookup FastAPI                     # find the class
hce lookup FastAPI --calls             # what does it call?
hce lookup QuerySet --calls --depth 2  # two levels deep
```

### Text search
```bash
hce search "middleware"
hce search "authentication"
```

### Natural language query
```bash
hce query "how does request validation work"
hce query "what middleware handles CORS"
```

### Codebase overview
```bash
hce overview --top 20
```

All commands accept `--cache-dir <path>` if you're not in the source root, and `--json` for structured output.

## Use as an MCP server

```bash
pip install -e ".[server]"
hce server
```

This exposes five MCP tools: `hce_lookup`, `hce_search`, `hce_query`, `hce_overview`, `hce_stats`.

## Re-indexing

The cache persists across sessions. Re-index only when the codebase changes significantly:
```bash
hce index ./fastapi/fastapi --skip-summaries  # overwrites existing cache
```

For incremental changes, the existing index is still useful — HCE uses file-hash caching to skip unchanged files.

## Limitations

- **Static analysis** — dynamic dispatch, monkey-patching, reflection, and similar runtime tricks aren't captured by tree-sitter's AST parsing.
- **Structure not semantics** — the graph tells you what calls what, not why. Read the code for business logic.
- **Language coverage** — 10 languages have full tree-sitter support (Python, JS, TS, Go, Rust, Java, C, C++, Ruby, PHP). Other languages fall back to a regex extractor that captures definitions and imports but misses calls and inheritance.
