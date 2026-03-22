# Hypergraph Code Explorer v3 — Implementation Plan

## Vision

The hypergraph is a **precomputed structural map** of a codebase, not a search engine. Instead of competing with grep and file reading (which LLM agents already do well), it provides what they can't: instant access to precomputed relationships — call chains, inheritance trees, dependency graphs — that would otherwise take an agent 3-5 rounds of grep-read-think to discover.

The output is a **retrieval plan**: file paths to read, symbols to grep for, relationships to follow, and context about why each matters. The consuming tool (Claude Code, Cursor, Codex, Cowork) uses its own file-reading capabilities to verify and act on the plan.

## What Changes

### KEEP (no changes)

| Module | Why |
|--------|-----|
| `models.py` — `HyperedgeRecord`, `EdgeType` | Core data model is solid. Directed hyperedges with source/target are exactly right. |
| `extraction/code_extractor.py` | AST extraction is the project's strongest asset. Zero LLM cost, precise, deterministic. |
| `graph/builder.py` | Inverted index + incidence dict + IDF/hub detection. This IS the structural map. |
| `ingestion/converter.py` | File discovery and markdown conversion. Works fine. |
| `ingestion/chunker.py` | Content-aware chunking. Needed for chunk_text on edges. |
| `graph/simplify.py` | Node deduplication. Still useful. |
| `graph/summaries.py` | File-level LLM summaries. Useful for Tier 5 (overview). |
| `pipeline.py` — indexing half | `index_directory()` pipeline is solid. Keep entirely. |

### REPLACE

| Module | What changes | Why |
|--------|-------------|-----|
| `retrieval/intersection.py` | Replace with `retrieval/dispatch.py` + tier modules | Embedding-scored retrieval → tiered structural dispatch |
| `retrieval/context.py` | Replace with `retrieval/plan.py` | Scored-edge display → structured retrieval plan |
| `retrieval/pathfinder.py` | Replace with `retrieval/traverse.py` | Generic BFS → relationship-typed structural traversal |
| `retrieval/coverage.py` | Remove (or simplify into a plan quality check) | Embedding-based coverage metric no longer meaningful |
| `graph/embeddings.py` | Move to Tier 4 only; remove from core index pipeline | Embeddings become optional fallback, not primary retrieval |
| `cli.py` | Rewrite with new commands | `index/query/server` → `index/lookup/search/overview/init/server` |
| `mcp_server.py` | Update tools to use new retrieval | Expose retrieval plan instead of scored edges |
| `api.py` | Update to new retrieval interface | Thin wrapper over new dispatch |
| `pipeline.py` — query half | Replace `query()` with dispatch-based retrieval | `retrieve()` → `dispatch()` → tiered plan |

### NEW

| Module | Purpose |
|--------|---------|
| `retrieval/dispatch.py` | Query classifier + tier router |
| `retrieval/lookup.py` | Tier 1: exact name lookup via inverted index |
| `retrieval/traverse.py` | Tier 2: relationship-typed BFS/DFS |
| `retrieval/textsearch.py` | Tier 3: substring/regex search over all node names |
| `retrieval/semantic.py` | Tier 4: embedding fallback (thin wrapper over embeddings.py) |
| `retrieval/plan.py` | RetrievalPlan data model + formatters (YAML, text, JSON) |
| `codemap.py` | CODEBASE_MAP.md generator (run after indexing) |
| `init.py` | `hce init` — drops tool-specific instruction files |

---

## Architecture

### Data Flow (Index Time)

```
Source Files → Converter → Chunker → AST Extractor → HypergraphBuilder
                                                          ↓
                                                    Inverted Index
                                                    IDF / Hub nodes
                                                    Simplification
                                                    Summaries (optional)
                                                          ↓
                                                    Save to .hce/
                                                          ↓
                                                    Generate CODEBASE_MAP.md
```

Embeddings are NOT computed at index time by default. They're computed lazily on first Tier 4 query, or explicitly via `hce embed`.

### Data Flow (Query Time)

```
Query → Dispatcher (classify query type)
            ↓
    ┌───────┼───────────┬──────────────┐
    ↓       ↓           ↓              ↓
  Tier 1  Tier 2      Tier 3        Tier 4
  Exact   Structural  Text Search   Embedding
  Lookup  Traversal   (substring)   (semantic)
    ↓       ↓           ↓              ↓
    └───────┴───────────┴──────────────┘
                    ↓
              RetrievalPlan
           (files, symbols, grep patterns,
            relationships, context)
```

### On-Disk Structure

```
project/
  .hce/
    builder.pkl          # hypergraph (nodes, edges, inverted index)
    embeddings.pkl       # vectors (only if Tier 4 has been used)
    manifest.json        # file-hash cache for incremental reindex
    CODEBASE_MAP.md      # static overview for agent context inclusion
```

---

## Tier Design

### Tier 1 — Exact Lookup (microseconds)

**Trigger:** Query contains tokens that exactly match node names in the graph.

**Process:**
1. Tokenise query (split on whitespace, dots, underscores)
2. Match tokens against `builder._node_to_edges` keys (case-insensitive)
3. For each matched node, return all incident edges grouped by type

**Example:**
```
Query: "How does Session.send work?"
Match: "Session.send" → node exists
Result: DEFINES edge (sessions.py defines Session.send)
        CALLS edge (Session.send calls get_adapter, adapter.send, ...)
        SIGNATURE edge (if present)
```

**Output fields populated:** `primary_files`, `grep_suggestions`, `related_symbols`, `structural_context`

### Tier 2 — Structural Traversal (milliseconds)

**Trigger:** Query contains relationship verbs (calls, imports, inherits, raises, uses, depends on) + an identified symbol. OR: Tier 1 found seed nodes and we want to expand.

**Process:**
1. Start from Tier 1 seed edges
2. BFS/DFS through the hypergraph following only edges of relevant types
3. Configurable depth (default 2)
4. Relationship type inferred from query verb:
   - "calls/invokes/delegates to" → follow CALLS edges
   - "inherits/extends/subclasses" → follow INHERITS edges
   - "imports/depends on/requires" → follow IMPORTS edges
   - "raises/throws" → follow RAISES edges
   - no verb → follow all types (general exploration)

**Example:**
```
Query: "What does Session.send call, and what do those call?"
Verb: "call" → CALLS edges only
Depth: 2
Result: Session.send → [get_adapter, adapter.send, merge_environment_settings, resolve_redirects]
        HTTPAdapter.send → [urllib3.send, build_response, ...]
```

**Output fields populated:** `files_to_read` (with priorities), `call_chain_summary`, `grep_suggestions`

### Tier 3 — Text Search (milliseconds)

**Trigger:** No exact node match, but query terms are substrings of node names, file paths, or chunk text.

**Process:**
1. Substring match query terms against:
   - All node names (case-insensitive)
   - All file paths (stem matching)
   - All `chunk_text` fields (grep-like)
   - All `relation` strings
2. Rank by match quality (exact stem > prefix > substring > chunk_text)
3. Feed top matches into Tier 1/2 for structural expansion

**Example:**
```
Query: "How does authentication work?"
Matches: "auth" → auth.py, AuthBase, HTTPBasicAuth, HTTPDigestAuth, rebuild_auth
Feed into Tier 1 → return DEFINES, INHERITS, CALLS edges for each
```

### Tier 4 — Embedding Fallback (tens of milliseconds)

**Trigger:** Tiers 1-3 produce zero results. Query has no lexical overlap with anything in the graph.

**Process:**
1. Lazy-load embeddings (compute on first use if not cached)
2. Embed query, find top-k similar nodes
3. Feed results into Tier 1 for structural expansion
4. Mark confidence as "approximate" in the plan

**Example:**
```
Query: "How does the library handle network failures?"
No match for "network failures" in nodes
Embedding finds: ConnectionError (0.62), Timeout (0.58), retry (0.55)
Feed into Tier 1 → return edges for those nodes
```

### Tier 5 — Overview (milliseconds)

**Trigger:** Query asks about architecture, structure, components, or is very broad.

**Process:**
1. Use SUMMARY edges (file-level summaries) if present
2. Compute degree centrality to identify most-connected nodes
3. Group by file/module
4. Return reading order suggestion

---

## RetrievalPlan Data Model

```python
@dataclass
class RetrievalPlan:
    query: str
    classification: list[str]       # ["identifier", "structural"] etc.
    tiers_used: list[int]            # [1, 2] etc.

    primary_files: list[FileSuggestion]
    grep_suggestions: list[GrepSuggestion]
    related_symbols: list[SymbolRelation]
    structural_context: str          # natural language summary
    overview: Overview | None        # for Tier 5

@dataclass
class FileSuggestion:
    path: str
    symbols: list[str]
    reason: str
    priority: int                    # 1 = most important

@dataclass
class GrepSuggestion:
    pattern: str                     # regex
    scope: str                       # file or directory glob
    reason: str

@dataclass
class SymbolRelation:
    name: str
    file: str
    relationship: str                # "calls", "inherits from", "imported by"
    edge_type: str                   # EdgeType value
```

---

## CLI Commands

### Existing (modified)

```
hce index <path> [--skip-summaries] [--verbose]
    # Same as before, but also generates CODEBASE_MAP.md
    # Embeddings NOT computed by default (add --embed to force)
```

### New

```
hce lookup <symbol> [--calls] [--callers] [--inherits] [--imports] [--raises] [--depth N]
    # Tier 1 + Tier 2. Exact symbol lookup + structural traversal.
    # Returns: file paths, relationships, grep suggestions.
    # Default depth: 1. Max depth: 5.

hce search <term> [--type TYPE] [--file GLOB]
    # Tier 3. Text search across node names, file paths, chunk text.
    # Returns: matching nodes/edges + structural context.

hce query <natural language question>
    # Full dispatch: classifies query, routes through all tiers.
    # Returns: complete RetrievalPlan as formatted text.
    # This is what the LLM agent calls via Bash.

hce overview [--top N]
    # Tier 5. Module summary, key symbols, reading order.
    # Default top: 10 most-connected symbols.

hce init [--tool claude-code|cursor|codex|all]
    # Generate tool-specific instruction file.
    # claude-code → CLAUDE.md
    # cursor → .cursorrules  (or .cursor/rules if project uses that)
    # codex → AGENTS.md
    # all → all three
    # If file exists, append hce section (don't overwrite).

hce embed [--force]
    # Explicitly compute/refresh embeddings for Tier 4.
    # Skips if embeddings.pkl exists and is current (unless --force).

hce stats
    # Node count, edge count, type breakdown, hub nodes, etc.
```

### Server (kept, updated)

```
hce server
    # MCP server exposing dispatch-based retrieval.
    # Tools: hce_lookup, hce_search, hce_query, hce_overview, hce_stats
```

---

## CODEBASE_MAP.md Generator

Generated automatically after `hce index`. Structure:

```markdown
# Code Map
<!-- Auto-generated by hce. Regenerate with: hce index <path> -->

## Modules
- requests/sessions.py — Central orchestrator. Session class coordinates requests.
- requests/adapters.py — Transport layer. HTTPAdapter wraps urllib3.
- requests/models.py — Data models. Request, PreparedRequest, Response.
...

## Key Symbols (top 100 by connectivity)
| Symbol | File | Degree |
|--------|------|--------|
| sessions.Session | sessions.py | 24 |
| adapters.HTTPAdapter | adapters.py | 18 |
...

## Call Chains (top 20 by depth)
- api.get → Session.request → Session.send → HTTPAdapter.send
- Session.send → merge_environment_settings → resolve_redirects
...

## Inheritance Trees (top 10)
- AuthBase ← HTTPBasicAuth, HTTPDigestAuth, HTTPProxyAuth
- BaseAdapter ← HTTPAdapter
...

## CLI Quick Reference
  hce lookup Session.send --calls    # what does Session.send call?
  hce lookup AuthBase --inherits     # what inherits from AuthBase?
  hce search "retry"                 # find retry-related code
  hce query "how does error handling work?"
  # Add --json to any command for structured output
```

**Content rules:**
- Modules section uses SUMMARY edge text if available, otherwise a one-line description derived from DEFINES edges.
- Key Symbols capped at top 100 by degree. Full list via `hce stats` or `hce search`.
- Call Chains capped at top 20 by depth. Full traversal via `hce lookup <symbol> --calls --depth N`.
- Inheritance Trees capped at top 10. Full hierarchy via `hce lookup <symbol> --inherits`.
- No line numbers anywhere. Agents use grep to find current positions.

---

## Tool Instruction Files

### CLAUDE.md (Claude Code)

```markdown
## Code Intelligence

This project is indexed by Hypergraph Code Explorer (hce). A structural
map is at `.hce/CODEBASE_MAP.md` — read it first when you need to
understand the project or find where something lives.

For deeper queries, use the CLI (available as `hce` in this project):

  hce lookup <symbol>              # where is it defined? what edges touch it?
  hce lookup <symbol> --calls      # what does it call?
  hce lookup <symbol> --callers    # what calls it?
  hce lookup <symbol> --inherits   # class hierarchy
  hce lookup <symbol> --depth 2    # follow relationships 2 hops
  hce search <term>                # text search across all symbols
  hce query "natural language"     # full dispatch query
  hce overview                     # module summary + reading order

Output is human-readable text by default. Add --json for structured
output when you need to parse the results programmatically.

The CLI returns file paths, related symbols, and grep patterns.
Prefer `hce lookup` over exploratory grepping when investigating
code relationships — it already knows the call graph and dependency
structure.

If hce returns nothing relevant, fall back to Grep/Read as normal.
```

### .cursorrules (Cursor)

```markdown
## Code Intelligence

This project is indexed by hce (Hypergraph Code Explorer).
Read `.hce/CODEBASE_MAP.md` for a structural overview.

When investigating code relationships, use the terminal:

  hce lookup <symbol> --calls      # call graph
  hce lookup <symbol> --inherits   # class hierarchy
  hce search <term>                # find symbols by name
  hce query "question"             # natural language query

Output is human-readable by default. Add --json for structured output.
The CLI returns file paths, related symbols, and grep patterns.
Use these to navigate directly to relevant code.
```

### AGENTS.md (Codex)

```markdown
## Code Intelligence

This project has a structural index at `.hce/CODEBASE_MAP.md`.
Read it for an overview of modules, key symbols, and relationships.

Use the `hce` CLI for structural queries:

  hce lookup <symbol> --calls --depth 2
  hce search <term>
  hce query "natural language question"
  hce overview

Output is human-readable by default. Add --json for structured output.
Returns file paths, related symbols, and grep patterns.
```

---

## Dependencies Changes

### Remove from required:
- `sentence-transformers` → move to optional `[embed]` extra
- `hypernetx` → not used (we have our own builder)
- `instructor` → only needed for TEXT edges (keep optional)
- `langchain-text-splitters` → check if still used by chunker

### Keep:
- `anthropic` → summaries
- `python-dotenv` → env loading
- `networkx` → check if actually used; if not, remove
- `numpy` → still needed for embeddings (optional tier)
- `markitdown` → file conversion
- `pydantic` → check if used; models.py uses dataclasses
- `mcp` → server mode

### pyproject.toml extras:
```toml
[project.optional-dependencies]
embed = ["sentence-transformers>=3.0.0", "numpy>=1.26.0"]
server = ["mcp>=1.0.0"]
all = ["hypergraph-code-explorer[embed,server]"]
```

---

## Implementation Order

### Phase 1 — New retrieval core (no breaking changes yet)

1. **`retrieval/plan.py`** — RetrievalPlan data model + formatters
2. **`retrieval/lookup.py`** — Tier 1 exact lookup
3. **`retrieval/traverse.py`** — Tier 2 structural BFS
4. **`retrieval/textsearch.py`** — Tier 3 text search
5. **`retrieval/dispatch.py`** — Query classifier + tier router
6. Tests for each tier independently

### Phase 2 — CLI and output

7. **`cli.py`** rewrite — `lookup`, `search`, `query`, `overview` commands
8. **`codemap.py`** — CODEBASE_MAP.md generator
9. **`init.py`** — `hce init` command
10. Integration tests: index → lookup → verify output

### Phase 3 — Clean up

11. Move `embeddings.py` to optional Tier 4 (`retrieval/semantic.py`)
12. Update `pipeline.py` — remove embedding from default index, add codemap generation
13. Update `mcp_server.py` — expose new tools
14. Update `api.py` — new interface
15. Prune dependencies in `pyproject.toml`
16. Remove old `intersection.py`, `context.py`, `coverage.py` (or archive)

### Phase 4 — Tool integration testing

17. Test with Claude Code (CLAUDE.md + CLI)
18. Test with Cursor (.cursorrules + CLI)
19. Test with Codex (AGENTS.md + CLI)
20. Update ARCHITECTURE.md

---

## Success Criteria

1. `hce lookup Session.send --calls` returns the correct call targets with file paths in < 100ms
2. `hce search "auth"` returns all auth-related symbols and files in < 100ms
3. `hce query "how does session send work"` produces a useful retrieval plan that an agent can act on
4. `hce overview` produces a readable module summary
5. CODEBASE_MAP.md gives an agent enough context to know when to call `hce`
6. All existing tests still pass (extraction, builder, models)
7. `hce init --tool all` produces valid CLAUDE.md, .cursorrules, AGENTS.md
8. No embedding model required for Tiers 1-3 (faster index, no HF dependency by default)

---

## Design Decisions (Resolved)

1. **No line numbers in CODEBASE_MAP.md or CLI output.** Line numbers go stale after edits and create false precision. The agent should use grep/read to find current line positions. File paths and symbol names are stable identifiers.

2. **Human-readable text output by default, `--json` flag for structured consumption.** Default output looks like grep — scannable by humans and LLMs alike. The `--json` flag produces structured output for programmatic use. Tool instruction files tell the LLM that `--json` is available.

3. **No scoring.** Tiers 1-3 return deterministic results — a symbol is in the graph or it isn't, BFS reaches a node or it doesn't. No wp, no coverage, no alpha, no confidence tiers. Tier 4 (embedding fallback) ranks by similarity internally but does not expose scores to the consumer. The complex scoring formula from v2 is removed entirely.

4. **Tree-sitter unified backend.** All languages go through tree-sitter for extraction (Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, PHP). The original Python AST extractor is preserved in `_legacy_python_extractor.py` for reference. A minimal regex fallback handles truly unsupported file types. Tiers 1-3 operate on the graph, not source, so they work regardless of language.

5. **CODEBASE_MAP.md capped for large codebases.** Top 100 symbols by degree, top 20 call chains, top 10 inheritance trees. Full data always available via `hce lookup`, `hce search`, etc. This keeps the map under ~500 lines even for large monorepos.
