# Hypergraph RAG v2 — Architecture Plan

## Design Thesis

The core differentiator of hypergraph RAG over standard knowledge-graph RAG is **edge-intersection traversal**: the ability to discover that two higher-order relationships are connected because they share members, and to use the shared members (intersection nodes) as the conceptual bridge that explains *why* they are connected. Everything in v2 is designed around making that traversal the primary retrieval and reasoning mechanism.

v1 treated hyperedges as fancy document chunks and scored them by node overlap — a regular knowledge graph could produce identical results. v2 inverts this: retrieval operates in **hyperedge space**, paths are found via edge-to-edge intersection, and context assembly preserves the traversal structure so the consuming agent can follow the reasoning chain.

---

## What We Keep From v1

These components proved their value and carry forward largely unchanged:

- **AST-based code extraction** — zero-cost, zero-hallucination structural facts (CALLS, IMPORTS, DEFINES, INHERITS, SIGNATURE, RAISES, DECORATES). This is the strongest part of v1.
- **HyperNetX** as the hypergraph data structure — handles N-ary edges, incidence dicts, subgraph restriction, union operations.
- **Sentence-transformer embeddings** (`all-MiniLM-L6-v2`) — 384-dim, trained for semantic similarity. Replaced all-MiniLM-L6-v2 after testing showed that all-MiniLM-L6-v2's mean-pooling on short identifiers produces degenerate embeddings (mean pairwise similarity ~0.95, causing 91% node merging). MiniLM is 6× smaller, faster, and actually trained for the similarity task we need. Node names are embedded with source-file context prepended (e.g. `"sessions.py: Session.send"`) to disambiguate short identifiers.
- **markitdown** for document conversion — handles PDFs, pptx, xlsx, docstrings.
- **Content-aware chunking** — AST-boundary splitting for code, heading/paragraph splitting for docs.
- **MCP server architecture** — tools do retrieval, the consuming agent does reasoning.
- **uv + pyproject.toml** — package management.
- **Cursor rules file** — `.cursor/rules/hypergraph.mdc` teaching the agent when and how to use the tools.

## What Changes

### 1. Edge Store — directed, with source/target metadata

v1's `EdgeStore` stored `edge_id`, `relation`, `edge_type`, `nodes` (flat set), and `source_path`.

v2 adds directionality and richer metadata per edge:

```python
@dataclass
class HyperedgeRecord:
    edge_id: str              # unique, normalized
    relation: str             # verb/predicate text
    edge_type: str            # CALLS, IMPORTS, DEFINES, TEXT, etc.
    sources: list[str]        # directed: subject entities
    targets: list[str]        # directed: object entities
    all_nodes: set[str]       # sources ∪ targets (for intersection math)
    source_path: str          # originating file
    chunk_id: str             # which chunk produced this edge
    chunk_text: str           # the actual source text (not just an ID)
```

Why: The MIT code tracks `source_map` and `target_map` per edge, and uses them in `generate_directional_sentence()` to produce readable traversal explanations. Without directionality, intersection reports can't tell the agent *what role* the shared nodes play.

### 2. Inverted Index — node → incident edges

v1 built this implicitly during retrieval. v2 makes it a first-class data structure maintained by the builder:

```python
class HypergraphBuilder:
    _incidence: dict[str, set[str]]       # edge_id → node set
    _node_to_edges: dict[str, set[str]]   # node → set of edge_ids  ← NEW
    _edge_store: dict[str, HyperedgeRecord]
    _chunk_registry: dict[str, str]
```

Why: Edge-intersection BFS needs to go from a node to all its incident edges instantly. Building this at index time (O(E)) avoids rebuilding it at query time.

### 3. Scoring Philosophy — Recall First, Agent Evaluates

The consuming agent (Cursor running Claude Opus 4.6 or a frontier coding model) is a far stronger filter than any scoring formula. A noisy result set is recoverable — the agent can identify and discard irrelevant edges. A missing edge is unrecoverable — if it never enters the context window, the agent never had a chance.

**Design principle: optimise for recall, not precision ranking.**

This means generous retrieval bounds (high top-K, low score thresholds) and a scoring formula that degrades gracefully at the edges of the parameter space rather than collapsing into a valley.

**The harmonic mean (F1) problem:** F1 requires both precision and coverage to be high simultaneously. For rare or peripheral entities — where coverage will necessarily be low — F1 scores nearly identically to an irrelevant edge. This creates a U-shaped performance curve: excellent for well-connected central entities, cliff-drop for outliers.

**The fix — additive combination:**

```
score = (α × weighted_precision + (1−α) × coverage) × type_weight

where:
  weighted_precision = Σ(sim_score of matched nodes in edge) / |edge|
  coverage           = count(matched nodes in edge) / K
  α                  = 0.6 by default (configurable)
```

Additive has no valley. High precision alone or high coverage alone both produce a reasonable score. α controls the trade-off and is exposed as a config parameter for empirical tuning in Phase 5.

### 4. Retrieval — edge-intersection traversal (THE CORE CHANGE)

**v1 algorithm:**
1. Embed query → find top-K similar nodes
2. For each edge, score = (matched nodes in edge) / (total nodes in edge) × type_weight
3. Return top edges by score

**v2 algorithm:**

**Phase 1 — Seed edge selection:**
1. Embed query → find top-K similar nodes (generous K, default 20)
2. Collect all edges incident on those nodes → seed edge set
3. Score seed edges using additive formula (see §3 above)
4. Tag each seed edge: `retrieval_source = "seed"`

**Phase 2 — Intersection expansion:**
5. For each seed edge, find all edges that **intersect** it (share ≥ `s` nodes, per edge type)
6. Score expanded edges by **intersection quality**:
   - `intersection_nodes = seed_edge.all_nodes ∩ expanded_edge.all_nodes`
   - `intersection_score = |intersection_nodes| × avg(sim_score of intersection_nodes)`
7. Tag each expanded edge: `retrieval_source = "intersection"`
8. Combine seed and intersection scores: `final_score = α × seed_score + (1−α) × intersection_score`

**Phase 3 — Traversal path construction:**
9. For the top-scoring edges, build traversal paths by following intersection chains:
   - Start from highest-scoring seed edge
   - At each hop, follow the highest-scoring intersection to the next edge
   - Record the intersection nodes at each hop (these are the conceptual bridges)
   - Stop when no more high-scoring intersections exist or max hops reached
10. Return:
    - `matched_nodes` — with similarity scores
    - `retrieved_edges` — with final scores and `retrieval_source` tags
    - `traversal_paths` — ordered list of edge hops with intersection nodes
    - `context_text` — assembled from traversal structure with evaluation signals (see §6)

### 5. Path Finding — edge-BFS (replacing node-BFS)

v1 did node-level BFS: start at node A, hop through shared edges to reach node B.

v2 does **edge-level BFS** following the MIT paper's `find_shortest_path_hypersubgraph_between_nodes_local`:

1. Find all edges containing the source entity → source edge set
2. Find all edges containing the target entity → target edge set
3. BFS through hyperedge space: two edges are adjacent if they intersect (share ≥ `s` nodes, default s=1)
4. At each hop, record the **intersection nodes** between consecutive edges
5. Return up to `k_paths` shortest paths (not just one)

The output is a list of path reports:
```python
{
    "pair": ("Session", "HTTPAdapter"),
    "edge_path": ["e_calls_session_send", "e_defines_httpadapter", ...],
    "hops": [
        {
            "from_edge": "e_calls_session_send",
            "to_edge": "e_defines_httpadapter",
            "intersection_nodes": ["HTTPAdapter", "poolmanager"],
            "from_members": ["Session", "send", "HTTPAdapter", "poolmanager"],
            "to_members": ["HTTPAdapter", "poolmanager", "send_request", "cert_verify"],
        },
        ...
    ]
}
```

This is fundamentally more informative than a flat node path like `["Session", "HTTPAdapter"]` — the intersection nodes explain *why* the connection exists.

### 6. Context Assembly — preserving traversal structure with evaluation signals

v1 assembled `context_text` by concatenating node names + edge relations + source chunks in score order. This threw away the traversal structure.

v2 assembles context that reflects the reasoning chain and includes structured evaluation signals so the consuming agent can assess coverage and decide whether follow-up queries are needed:

```
=== RETRIEVAL SUMMARY ===
Query: "how does session authentication work"
Seed nodes matched: 7 of 20 requested (coverage: moderate)
Traversal paths: 2
Total edges: 8 (5 seed, 3 intersection)
Coverage score: 0.61  ← agent reads this to decide if follow-up is needed
Uncovered frontier nodes: [AuthBase, HTTPDigestAuth, _auth]  ← suggested next queries

=== Traversal Path 1 ===

[Edge 1] CALLS: Session.send → HTTPAdapter.send  [seed | score: 0.87 | HIGH confidence]
  Source: requests/sessions.py lines 623-650
  ---
  <source code excerpt>
  ---

  ↓ connected via: {HTTPAdapter, poolmanager}

[Edge 2] DEFINES: HTTPAdapter members  [intersection | score: 0.72 | MED confidence]
  Source: requests/adapters.py lines 45-120
  ---
  <source code excerpt>
  ---

=== Additional Relevant Edges ===

[Edge 3] IMPORTS: sessions imports adapters  [seed | score: 0.65 | MED confidence]
  ...
```

Key additions over v1:
- **Retrieval summary header** — coverage score, counts, and frontier nodes give the agent a bird's-eye view before it reads the evidence
- **"connected via: {intersection_nodes}"** line between consecutive edges — explains why two pieces of evidence are related
- **`[seed | score | confidence tier]` tags per edge** — agent knows whether an edge was directly matched (`seed`) or discovered by traversal (`intersection`), and can calibrate trust accordingly
- **Frontier nodes** in the header — nodes on the edge of the retrieved subgraph that were not themselves retrieved. These are the most likely targets for a follow-up query.

### 7. Neighbours — edge-intersection expansion

v1's `hypergraph_neighbors` expanded via shared node membership (node-centric).

v2 expands via edge intersection (edge-centric):
1. Find all edges incident on the query node
2. For each incident edge, find all edges that intersect it
3. Return the expanded edge neighbourhood with intersection details
4. Group results by the intersection nodes that connect them

This gives the agent a structural explanation: "Session connects to HTTPAdapter *through* the edges about send/receive, which share the poolmanager concept."

### 8. Agent Evaluation Loop — structured self-assessment

The recall-first philosophy means the agent will sometimes receive context with gaps. Rather than assuming one retrieval pass is always sufficient, v2 gives the agent a lightweight, purely local tool to evaluate its own coverage and decide whether a follow-up query is warranted.

**`hypergraph_coverage` tool** (no LLM calls, purely graph-structural):

```python
# Input
{
    "retrieved_edge_ids": ["e_1", "e_2", ...],     # from the previous retrieve call
    "seed_node_ids": ["Session", "HTTPAdapter"],    # the matched_nodes from retrieve
    "depth": 1                                      # how far to look for frontier nodes
}

# Output
{
    "covered_nodes": ["Session", "HTTPAdapter", "send", "poolmanager"],
    "uncovered_nodes": ["AuthBase", "HTTPDigestAuth", "_auth"],
    "frontier_nodes": [                            # nodes 1 hop outside the retrieved subgraph
        {
            "node": "AuthBase",
            "incident_edge_count": 4,              # how many edges touch this node
            "suggested_query": "AuthBase authentication"
        },
        {
            "node": "HTTPDigestAuth",
            "incident_edge_count": 2,
            "suggested_query": "HTTPDigestAuth implementation"
        }
    ],
    "coverage_score": 0.61,                        # covered / (covered + uncovered)
    "intersection_density": 0.44,                  # fraction of edge pairs that share ≥1 node
    "retrieval_source_breakdown": {
        "seed": 5,
        "intersection": 3
    }
}
```

**Agent workflow using this tool:**

```
Step 1: hypergraph_retrieve(query, top_k=20)
        → read retrieval summary header, check coverage_score

Step 2: if coverage_score < 0.5 or frontier_nodes has high-degree nodes:
            hypergraph_coverage(retrieved_edge_ids, seed_node_ids)
            → identify the most important uncovered frontier nodes
            → pick the top 1-2 suggested_queries

Step 3: for each suggested follow-up query:
            hypergraph_retrieve(suggested_query, top_k=10)
            → merge into existing context

Step 4: synthesise answer from combined context,
        noting which claims are HIGH vs MED vs LOW confidence
        based on retrieval_source and score tiers
```

**Why this is enough:** The agent (Claude Opus 4.6 or equivalent) can judge sufficiency from the coverage signal without being told what "enough" means in abstract. It can decide when the frontier nodes are peripheral (low incident_edge_count) and one pass is fine, vs when they represent a major component that's been missed. The tool is cheap — O(retrieved_edges) — and can be called speculatively.

**What the cursor rules teach:** The agent doesn't need to always run the full loop. The heuristic: if `coverage_score > 0.7` and no frontier node has `incident_edge_count > 5`, proceed to synthesis. Otherwise run coverage check.

### 9. Module-Level Summaries — lightweight global context

The one idea worth borrowing from GraphRAG without adopting its full framework. GraphRAG's strength is hierarchical community summarisation — answering broad, thematic queries like "how does authentication work in this codebase?" where the user doesn't know the exact entry points. Our hypergraph excels at precise structural traversal but has no mechanism for this kind of zoomed-out question.

**The approach:** after the hypergraph is fully built (all edges extracted, simplified, and indexed), generate short natural-language summaries at the module/file level and store them as a special edge type that participates in the same retrieval pipeline.

**How it works:**

1. **Group edges by source file** — every `HyperedgeRecord` already carries `source_path`. Group all edges originating from the same file.

2. **Generate a summary per file** — feed the edge list to an LLM (Claude Haiku for cost, Sonnet for quality) with a focused prompt:

```python
# Summary generation prompt (per file)
"""
Given these structural relationships extracted from {source_path}:

{edge_list_formatted}

Write a 2-3 sentence summary of what this file does, what its key responsibilities are,
and what other parts of the codebase it connects to. Be specific — name the important
classes, functions, and modules. Do not be generic.
"""
```

3. **Store as SUMMARY edges** — each summary becomes a `HyperedgeRecord` with:

```python
HyperedgeRecord(
    edge_id="summary__requests/sessions.py",
    relation="summarises",
    edge_type="SUMMARY",
    sources=[source_path],              # the file being summarised
    targets=[],                         # no targets — this is a description, not a relationship
    all_nodes=extracted_key_entities,    # the 3-5 most important entity names mentioned
    source_path=source_path,
    chunk_id=f"summary__{source_path}",
    chunk_text=summary_text,            # the generated summary itself
    metadata={"summary_level": "file", "edge_count": len(grouped_edges)}
)
```

4. **Embed normally** — the summary text gets embedded via all-MiniLM-L6-v2 alongside everything else. Because summaries mention the key entity names from the file, they naturally appear in retrieval results for broad queries.

5. **Optional: directory-level roll-ups** — for large codebases, generate a second tier by feeding file-level summaries from the same directory into a roll-up prompt. Store as `summary_level: "directory"`. This gives a two-level hierarchy (file → directory) without the full Leiden clustering machinery.

**Why this is enough (and why we don't need full GraphRAG):**

- **Community detection is overkill for code.** Code already has a natural hierarchical structure: files → directories → packages. We don't need an algorithm to discover communities — the filesystem *is* the community structure.
- **Summaries participate in existing retrieval.** No separate retrieval pipeline, no routing decision. The same `hypergraph_retrieve` call returns SUMMARY edges alongside CALLS and TEXT edges. The agent sees them in context and can use them as orientation before diving into structural edges.
- **The agent loop closes remaining gaps.** If a broad query hits a file summary, the agent reads the summary, identifies the key entities mentioned, and issues a targeted follow-up query. The coverage tool (§8) confirms whether the follow-up was sufficient. This is more adaptive than GraphRAG's one-shot community search.

**When summaries help most:**

- "What does this codebase do?" — no seed entities at all, needs orientation
- "How does authentication work?" — broad concept, many possible entry points
- "What changed in the last refactor?" — needs module-level understanding before edge-level detail
- Code review — the agent needs to understand a file's role before judging a diff

**When summaries don't help (and that's fine):**

- "How does Session.send() call HTTPAdapter?" — precise structural query, edge-intersection traversal handles this directly
- "Find the path from auth to connection pooling" — edge-BFS handles this

**Scoring note:** SUMMARY edges should have a low `type_weight` in the additive formula (proposed: 0.3 vs 1.0 for AST and 0.7 for TEXT). They're orientation context, not evidence. The agent should rank them below structural edges for precise queries but still see them for broad ones.

### 10. Node Simplification — embedding-based merge

Adopted from the MIT code's `simplify_hypergraph`: after building the graph, find pairs of nodes with cosine similarity above a threshold (0.9) and merge the lower-degree node into the higher-degree one. This:
- Collapses near-duplicate entities (`http_response` and `HTTPResponse`)
- Reduces noise from slightly different surface forms of the same concept
- Updates the incidence dict, edge store, and embeddings in one pass

### 11. File-Hash Caching for Incremental Updates

v1 re-extracted everything on every run.

v2 stores a manifest: `{file_path: (sha256_hash, [edge_ids])}`. On re-index:
- Unchanged files → skip
- Modified files → remove old edges, re-extract
- New files → extract and add
- Deleted files → remove edges

This makes the index viable for active codebases.

---

## Module Layout

```
hypergraph-rag-v2/
├── pyproject.toml
├── .env
├── .cursor/
│   ├── mcp.json
│   └── rules/
│       └── hypergraph.mdc
├── src/
│   └── hypergraph_rag/
│       ├── __init__.py              # public API exports
│       ├── models.py                # HyperedgeRecord, TraversalHop, PathReport, RetrievalResult
│       ├── ingestion/
│       │   ├── converter.py         # markitdown wrapper, file discovery, skip_hidden
│       │   └── chunker.py           # AST-boundary + heading/paragraph splitting
│       ├── extraction/
│       │   ├── code_extractor.py    # AST-based: CALLS, IMPORTS, DEFINES, INHERITS, SIGNATURE, RAISES, DECORATES
│       │   └── text_extractor.py    # Claude + instructor: S-V-O and semantic groups
│       ├── graph/
│       │   ├── builder.py           # HypergraphBuilder: incidence + inverted index + edge store
│       │   ├── embeddings.py        # all-MiniLM-L6-v2 via sentence-transformers
│       │   ├── simplify.py          # node merging by embedding similarity (from MIT code)
│       │   └── summaries.py         # module-level summary generation + SUMMARY edge creation  ← NEW
│       ├── retrieval/
│       │   ├── intersection.py      # edge-intersection scoring + traversal path construction  ← NEW
│       │   ├── pathfinder.py        # edge-BFS path finding (from MIT code)  ← NEW
│       │   ├── context.py           # traversal-aware context assembly with evaluation signals  ← NEW
│       │   └── coverage.py          # hypergraph_coverage: frontier nodes, coverage score  ← NEW
│       ├── pipeline.py              # HypergraphRAG: orchestrates everything
│       ├── api.py                   # HypergraphSession: serializable API for MCP
│       ├── mcp_server.py            # FastMCP server: 8 tools (adds hypergraph_coverage, hypergraph_summarize)
│       └── cli.py                   # argparse: index + query subcommands
├── scripts/
│   ├── test_pipeline.py
│   ├── query.py
│   └── visualize.py
└── ARCHITECTURE.md
```

Key structural changes from v1:
- `models.py` — all dataclasses in one place instead of scattered across modules
- `retrieval/` split into four files: `intersection.py` (the core scoring), `pathfinder.py` (edge-BFS), `context.py` (assembly), `coverage.py` (agent self-evaluation)
- `graph/simplify.py` — new module for node merging
- `graph/summaries.py` — new module for file/directory-level summary generation
- `retrieval/search.py` removed — replaced by `intersection.py`

---

## MCP Tool Interface Changes

### `hypergraph_retrieve` (updated return schema)

```python
{
    "query": "how does session authentication work",
    "matched_nodes": [{"node": "Session", "score": 0.92}, ...],
    "retrieved_edges": [
        {
            "edge_id": "...",
            "relation": "calls",
            "edge_type": "CALLS",
            "sources": ["Session"],
            "targets": ["HTTPAdapter"],
            "score": 0.87,
            "source_path": "requests/sessions.py"
        }, ...
    ],
    "traversal_paths": [                        # ← NEW
        {
            "edges": ["edge_1", "edge_2", ...],
            "hops": [
                {
                    "from_edge": "edge_1",
                    "to_edge": "edge_2",
                    "intersection_nodes": ["HTTPAdapter", "poolmanager"],
                }
            ]
        }
    ],
    "context_text": "..."                       # ← now traversal-structured
}
```

### `hypergraph_find_path` (updated to edge-BFS)

```python
{
    "source": "Session",
    "target": "HTTPAdapter",
    "edge_paths": [                             # ← was flat node path
        {
            "edge_path": ["e1", "e2", "e3"],
            "hops": [
                {
                    "from_edge": "e1",
                    "to_edge": "e2",
                    "intersection_nodes": ["HTTPAdapter"],
                    "from_members": [...],
                    "to_members": [...]
                }
            ],
            "start_comembers": [...],
            "end_comembers": [...]
        }
    ],
    "num_paths": 2
}
```

### `hypergraph_neighbors` (updated to edge-intersection expansion)

```python
{
    "resolved_node": "Session",
    "incident_edges": [...],
    "intersecting_edges": [                     # ← NEW
        {
            "edge_id": "...",
            "intersects_with": "...",
            "intersection_nodes": ["HTTPAdapter"],
            "relation": "...",
            "edge_type": "..."
        }
    ]
}
```

### `hypergraph_coverage` (NEW — agent self-evaluation)

```python
# Input parameters
retrieved_edge_ids: list[str]   # edges already retrieved
seed_node_ids: list[str]        # nodes already matched
depth: int = 1                  # frontier expansion depth

# Output
{
    "covered_nodes": [...],
    "uncovered_nodes": [...],
    "frontier_nodes": [
        {
            "node": str,
            "incident_edge_count": int,    # edges touching this node (importance signal)
            "suggested_query": str         # pre-formed query to retrieve this node's context
        },
        ...
    ],
    "coverage_score": float,               # covered / (covered + uncovered), 0–1
    "intersection_density": float,         # fraction of edge-pairs that intersect
    "retrieval_source_breakdown": {
        "seed": int,
        "intersection": int
    }
}
```

Call this when `coverage_score` in the retrieval summary is below 0.5, or when you want to confirm the context is complete before synthesising an answer. The `suggested_query` fields are ready to pass directly to `hypergraph_retrieve`.

### `hypergraph_summarize` (NEW — module-level summary generation)

```python
# Input parameters
scope: str = "file"             # "file" or "directory"
paths: list[str] | None = None  # specific files/dirs to summarize; None = all
force: bool = False             # regenerate even if summaries exist
model: str = "haiku"            # "haiku" (fast/cheap) or "sonnet" (higher quality)

# Output
{
    "summaries_generated": 12,
    "summaries_skipped": 34,       # already existed and force=False
    "summary_edges_created": [
        {
            "edge_id": "summary__requests/sessions.py",
            "source_path": "requests/sessions.py",
            "summary": "sessions.py implements the core Session class...",
            "key_entities": ["Session", "HTTPAdapter", "send", "merge_environment_settings"],
            "edge_count": 28         # structural edges that informed this summary
        },
        ...
    ],
    "total_cost_estimate_usd": 0.03  # approximate LLM cost for this run
}
```

Typically called once after indexing (or after re-indexing changed files). The agent does not call this during retrieval — it's a build-time operation. Summary edges are automatically included in `hypergraph_retrieve` results when they score above the threshold. The `force` parameter allows regenerating summaries after the graph has been re-indexed with new edges.

### `hypergraph_stats`, `hypergraph_list_nodes`, `hypergraph_list_edges` — unchanged

---

## Cursor Rules Update

The `.cursor/rules/hypergraph.mdc` file needs to teach the agent the new mental model:

**Old mental model (v1):** "Call `hypergraph_retrieve`, read `context_text`, compose your answer."

**New mental model (v2):** Three-stage retrieval loop — retrieve, evaluate, synthesise.

```
Stage 1 — RETRIEVE
  Call hypergraph_retrieve(query, top_k=20)
  Read the RETRIEVAL SUMMARY header:
    - coverage_score: below 0.5 means significant gaps likely
    - frontier_nodes: these are the entities just outside your context window

Stage 2 — EVALUATE (conditional)
  If coverage_score < 0.5 OR a frontier node has incident_edge_count > 5:
    Call hypergraph_coverage(retrieved_edge_ids, seed_node_ids)
    Identify the 1-2 most important uncovered frontier nodes
    Call hypergraph_retrieve(suggested_query) for each
  Otherwise: proceed to Stage 3

Stage 3 — SYNTHESISE
  Read traversal_paths to understand WHY pieces of evidence are connected
  The intersection_nodes between edges are the conceptual bridges
  Use confidence tiers (HIGH/MED/LOW) to calibrate claims in your answer
  Seed edges are directly matched to your query; intersection edges were discovered
    by traversal and may be more peripheral — treat with slightly lower confidence
```

The agent should understand:
- `intersection_nodes` between two edges are the explanation for why those edges are related — always mention them when explaining connections
- `traversal_paths` give a reasoning chain, not just a bag of evidence — follow the chain when composing answers
- `hypergraph_find_path` returns edge-level paths with intersection explanations, not just node lists
- `hypergraph_coverage` is cheap — call it speculatively when in doubt

**Working with SUMMARY edges:**

SUMMARY edges appear alongside structural edges in `hypergraph_retrieve` results. They are tagged with `edge_type: "SUMMARY"` and contain a natural-language description of a file or directory. The agent should treat them as orientation context:

```
When your retrieve results include SUMMARY edges:
  - Read them first for high-level orientation before diving into structural edges
  - They tell you what a file DOES, not the precise call chains — use them to
    decide which structural edges are most relevant to the user's question
  - If a broad query ("how does auth work?") returns mostly SUMMARY edges,
    extract the key entity names from the summaries and issue a targeted
    follow-up retrieve for those entities
  - SUMMARY edges have low type_weight (0.3) — they will naturally rank below
    structural edges for precise queries. If they're the only results, the query
    is probably broad and you should plan a drill-down strategy

When no SUMMARY edges appear:
  - The query is precise enough that structural edges answered it directly — good
  - No action needed — summaries are optional orientation, not required evidence
```

---

## Implementation Order

### Phase 1 — Core data structures and graph building
1. `models.py` — all dataclasses
2. `graph/builder.py` — incidence dict + inverted index + directed edge store
3. `ingestion/converter.py` — carry forward from v1 with skip_hidden fix
4. `ingestion/chunker.py` — carry forward from v1
5. `extraction/code_extractor.py` — carry forward from v1, populate directed source/target
6. `extraction/text_extractor.py` — carry forward from v1, populate directed source/target
7. `graph/embeddings.py` — all-MiniLM-L6-v2, carry forward from v1

### Phase 2 — The retrieval core (the hard part)
8. `retrieval/intersection.py` — additive seed scoring + intersection expansion + traversal path construction
9. `retrieval/pathfinder.py` — edge-BFS from MIT code, adapted for our edge store
10. `retrieval/context.py` — traversal-aware context assembly with evaluation signals in header
11. `retrieval/coverage.py` — `hypergraph_coverage`: frontier node detection, coverage score, suggested queries

### Phase 3 — Pipeline and interface
12. `graph/simplify.py` — node merging by embedding similarity
13. `graph/summaries.py` — module-level summary generation (file-level, optional directory roll-ups)
14. `pipeline.py` — orchestrator with file-hash caching, summary generation as final build step
15. `api.py` — HypergraphSession with updated return types
16. `mcp_server.py` — 8 tools with new schemas (adds `hypergraph_coverage`, `hypergraph_summarize`)
17. `cli.py` — index + query subcommands (add `--skip-summaries` flag)

### Phase 4 — Agent integration
18. `.cursor/rules/hypergraph.mdc` — three-stage retrieve/evaluate/synthesise mental model + summary awareness
19. `.cursor/mcp.json` — same as v1
20. `ARCHITECTURE.md` — comprehensive guide

### Phase 5 — Validation
21. Index psf/requests, compare retrieval quality vs v1
22. Test edge-BFS paths produce meaningful intersection explanations
23. Test broad queries ("how does authentication work?") hit SUMMARY edges and lead to useful follow-ups
24. Test on a larger codebase (500k+ lines) to validate scale advantage + summary quality at scale

---

## Dependencies

```toml
[project]
dependencies = [
    "anthropic>=0.46.0",
    "python-dotenv>=1.0.0",
    "hypernetx>=2.4.0",
    "networkx>=3.0",
    "numpy>=1.26.0",
    "sentence-transformers>=3.0.0",
    "markitdown[all]>=0.1.0",
    "pydantic>=2.0.0",
    "instructor>=1.0.0",
    "langchain-text-splitters>=0.3.0",
    "mcp>=1.0.0",
]
```

Dropped from v1: `einops` (nomic-specific), `fastjsonschema` (unused), `pandas` (not needed in core), `pyvis` (optional viz only).

---

## Key Decisions

**Resolved:**

- **Scoring formula** — Additive combination `(α × weighted_precision + (1−α) × coverage) × type_weight`, not harmonic mean (F1). Default α=0.6. Avoids the U-shaped valley that would penalise rare/peripheral entities. Exposed as a config parameter for Phase 5 tuning.

- **Retrieval philosophy** — Recall-first. Default top_k=20. The consuming agent (Claude Opus 4.6 or equivalent) filters noise; missing edges are the true failure mode.

- **Agent evaluation loop** — Structured, two-stage: retrieve + conditional coverage check via `hypergraph_coverage`. Threshold for triggering coverage check: `coverage_score < 0.5` OR `any frontier node has incident_edge_count > 5`.

- **Module-level summaries** — Lightweight global context borrowed from GraphRAG's playbook, without adopting its full community-detection framework. File-level summaries generated post-indexing, stored as SUMMARY edge type with low `type_weight` (0.3). Uses the filesystem's natural hierarchy (file → directory) instead of algorithmic community detection. Summaries participate in the same retrieval pipeline — no separate routing needed.

- **Intersection threshold `s` per edge type** — AST edges s=1, TEXT edges s=2. Confirmed by interactive analysis: AST identifiers are precise (any shared name is a real connection), TEXT noun phrases are vague (single shared tokens like "HTTP" create noise). See presentation §4.

- **Blending parameter `α`** — Default 0.6, configurable per query call. The MCP tool accepts α as an optional parameter. No reason to lock it down.

- **Text extraction for code-only corpora** — Opt-in. TEXT edges disabled by default for code repositories. Only generated when explicitly enabled via a config flag (`text_edges: true`). Keeps the pipeline simpler and avoids noisy LLM-extracted edges for pure code repos.

- **Node simplification threshold** — Start at 0.97 cosine similarity. With all-MiniLM-L6-v2 (which produces well-separated embeddings for short identifiers), this threshold prevents merging distinct names like `send` and `send_request`. Context-prefixed node names (e.g. `"sessions.py: Session.send"`) further improve separation. Transitive merge chains are broken — only directly similar pairs merge.

- **Summary generation model** — Default to Haiku (fast, ~$0.01 per 100 files). Expose a `--model` flag on `hypergraph_summarize` for users who want Sonnet quality. Summaries can be regenerated cheaply if the model choice needs revisiting.

- **Directory-level roll-ups** — Deferred to Phase 5. File-level summaries ship first. Directory roll-ups use the same architecture (second grouping pass over file summaries) and can be added with minimal code changes once file-level summaries are validated.

- **SUMMARY type_weight** — 0.3 (vs 1.0 for AST, 0.7 for TEXT). Keeps summaries below structural evidence for precise queries while surfacing them for broad ones. Subject to empirical tuning in Phase 5.

**All design decisions are now resolved. Ready to implement.**
