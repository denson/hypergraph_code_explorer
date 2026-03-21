# Hub Node Filtering: IDF-Weighted Intersection Scoring

## Problem

Traversal paths get polluted by ultra-common nodes like `int`, `isinstance`,
`getattr`, `len`, `ValueError`. These nodes appear in dozens of edges and
create spurious connections during intersection expansion and path construction.

Example: querying `"how does authentication work"` produces Path 2 jumping
from `is_valid_cidr` to `address_in_network` to `unquote_unreserved` —
all connected through the node `int`, which appears in 40+ edges. The connection
is structurally valid but semantically meaningless.

## Design Constraint: Must Generalise

We cannot hardcode a Python builtins blacklist. The solution must work for:
- Any programming language (Python, Java, Rust, Go, TypeScript)
- Small libraries (500 edges) and giant monorepos (50,000+ edges)
- Codebases where the hub nodes are domain-specific (e.g., a Django project
  where `request`, `response`, `settings` are hubs)

## Solution: Data-Driven IDF Weighting

Use **Inverse Document Frequency** from information retrieval. Each node's
informativeness is computed from its actual frequency in the graph:

```
degree(n) = number of edges containing node n
idf(n)    = log(1 + total_edges / degree(n))
```

This naturally adapts to any codebase:
- `int` (degree 40 in a 500-edge graph): idf = log(1 + 500/40) = 2.6
- `sessions.Session` (degree 5): idf = log(1 + 500/5) = 4.6
- `HTTPAdapter.send` (degree 2): idf = log(1 + 500/2) = 5.5

In a 50,000-edge monorepo, the same ratio produces the same IDF. No
language-specific knowledge needed.

## Implementation: Three Changes

### Change 1 — Add `compute_node_idf()` to `builder.py`

Add a method to HypergraphBuilder that computes IDF for all nodes. This uses
the existing `_node_to_edges` inverted index (degree = `len(_node_to_edges[node])`).

**File: `src/hypergraph_code_explorer/graph/builder.py`**

Add this method to the `HypergraphBuilder` class, in the `# ---- querying`
section (after `get_node_degree`, around line 148):

```python
def compute_node_idf(self) -> dict[str, float]:
    """Compute IDF (Inverse Document Frequency) for every node.

    idf(n) = log(1 + total_edges / degree(n))

    High-degree "hub" nodes (int, isinstance, etc.) get low IDF.
    Specific nodes (Session.send, HTTPAdapter) get high IDF.
    Adapts automatically to any codebase size and language.
    """
    import math
    total_edges = len(self._incidence)
    if total_edges == 0:
        return {}
    idf: dict[str, float] = {}
    for node, edge_ids in self._node_to_edges.items():
        degree = len(edge_ids)
        idf[node] = math.log(1 + total_edges / degree)
    return idf
```

Also add a method to identify hub nodes by degree percentile, for use as
a filter in adjacency calculations:

```python
def get_hub_nodes(self, max_degree_pct: float = 0.03) -> set[str]:
    """Return nodes whose degree exceeds max_degree_pct × total_edges.

    These "hub" nodes appear in so many edges that they create
    spurious adjacency connections. Default threshold: 3% of edges.

    Adapts to graph size:
      - 500 edges → threshold = 15 (nodes in 15+ edges are hubs)
      - 50,000 edges → threshold = 1,500
    """
    total_edges = len(self._incidence)
    threshold = max(2, int(total_edges * max_degree_pct))
    return {
        node for node, edge_ids in self._node_to_edges.items()
        if len(edge_ids) > threshold
    }
```

### Change 2 — Filter hub nodes in `get_adjacent_edges()`

**File: `src/hypergraph_code_explorer/graph/builder.py`**

Modify `get_adjacent_edges` to accept an optional `exclude_nodes` parameter.
When provided, these nodes are ignored for both candidate collection and
intersection computation. This prevents hub nodes from creating spurious
adjacency.

Replace the current `get_adjacent_edges` method (lines 114-136):

```python
def get_adjacent_edges(
    self,
    edge_id: str,
    s: int = 1,
    exclude_nodes: set[str] | None = None,
) -> list[tuple[str, set[str]]]:
    """
    Find edges sharing ≥ s nodes with the given edge.
    Returns list of (adjacent_edge_id, intersection_nodes).

    Args:
        edge_id: The edge to find neighbours for.
        s: Minimum intersection size.
        exclude_nodes: Nodes to ignore (e.g., hub nodes). These are not
            counted toward the intersection size and not included in the
            returned intersection sets. This prevents high-degree nodes
            like 'int' or 'isinstance' from creating spurious adjacency.
    """
    nodes = self._incidence.get(edge_id, set())
    if not nodes:
        return []

    # Filter out hub nodes for candidate collection
    effective_nodes = nodes - exclude_nodes if exclude_nodes else nodes
    if not effective_nodes:
        return []

    # Collect candidate edges via inverted index (only through non-hub nodes)
    candidate_edges: set[str] = set()
    for node in effective_nodes:
        candidate_edges.update(self._node_to_edges.get(node, set()))
    candidate_edges.discard(edge_id)

    results = []
    for cand_id in candidate_edges:
        cand_nodes = self._incidence.get(cand_id, set())
        intersection = effective_nodes & cand_nodes
        if exclude_nodes:
            intersection -= exclude_nodes
        if len(intersection) >= s:
            results.append((cand_id, intersection))

    return results
```

### Change 3 — Use IDF weighting in intersection scoring (`intersection.py`)

**File: `src/hypergraph_code_explorer/retrieval/intersection.py`**

Three sub-changes:

**3a. Compute IDF and hub set at the start of `retrieve()`.**

After line 62 (`it = intersection_thresholds or DEFAULT_INTERSECTION_THRESHOLDS`),
add:

```python
# Compute node IDF for intersection weighting and hub filtering
node_idf = builder.compute_node_idf()
hub_nodes = builder.get_hub_nodes(max_degree_pct=0.03)
```

**3b. Pass `exclude_nodes=hub_nodes` to all `get_adjacent_edges()` calls.**

In Phase 2 (line 117), change:
```python
# OLD:
adjacent = builder.get_adjacent_edges(eid, s=threshold)

# NEW:
adjacent = builder.get_adjacent_edges(eid, s=threshold, exclude_nodes=hub_nodes)
```

In `_build_traversal_paths` (line 218), change:
```python
# OLD:
adjacent = builder.get_adjacent_edges(current_eid, s=threshold)

# NEW:
adjacent = builder.get_adjacent_edges(current_eid, s=threshold, exclude_nodes=hub_nodes)
```

This means `_build_traversal_paths` needs `hub_nodes` passed in. Update its
signature and the call site:

```python
def _build_traversal_paths(
    scored_edges: list[ScoredEdge],
    builder: HypergraphBuilder,
    thresholds: dict[str, int],
    max_hops: int = 5,
    hub_nodes: set[str] | None = None,
) -> list[PathReport]:
```

And at the call site (around line 157):
```python
traversal_paths = _build_traversal_paths(
    sorted_edges, builder, it, max_hops=max_hops, hub_nodes=hub_nodes,
)
```

**3c. Replace `len(intersection_nodes)` with IDF-weighted count in Phase 2.**

In the Phase 2 intersection scoring (lines 127-130), change:

```python
# OLD:
int_sims = [node_scores.get(n, 0.0) for n in intersection_nodes]
avg_sim = sum(int_sims) / len(int_sims) if int_sims else 0.0
intersection_score = len(intersection_nodes) * avg_sim

# NEW:
# IDF-weighted intersection: specific nodes count more than generic ones
int_sims = [node_scores.get(n, 0.0) for n in intersection_nodes]
avg_sim = sum(int_sims) / len(int_sims) if int_sims else 0.0
idf_weight = sum(node_idf.get(n, 1.0) for n in intersection_nodes)
intersection_score = idf_weight * avg_sim
```

This means a connection through `sessions.Session` (IDF ~4.6) scores nearly
2× higher than a connection through `int` (IDF ~2.6), even before hub
filtering removes `int` from adjacency entirely. The IDF weighting is a
second line of defence for nodes that are high-degree but below the 3%
hub threshold.

**3d. Update the module docstring** to mention hub node filtering:

```python
"""
Edge-Intersection Retrieval
============================
THE CORE MODULE. Implements the full retrieval algorithm:
  Phase 1 — Seed edge selection via hybrid embedding/keyword similarity
  Phase 2 — Intersection expansion via shared nodes (hub-filtered, IDF-weighted)
  Phase 3 — Traversal path construction following intersection chains

Scoring: score = (α × wp + (1−α) × cov) × type_weight
  wp = mean_sim × sqrt(match_ratio)

Hub node filtering: nodes appearing in >3% of edges are excluded from
intersection calculations. Remaining intersection nodes are weighted by
IDF = log(1 + total_edges / degree) so that specific nodes (Session, HTTPAdapter)
contribute more than generic ones (isinstance, ValueError).
"""
```

## Testing

### New test: `test_hub_node_filtering.py`

Create a new test file `tests/test_hub_node_filtering.py`:

```python
"""Tests for hub node filtering and IDF weighting."""

import math
from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.models import HyperedgeRecord


def _make_edge(edge_id, sources, targets, edge_type="CALLS"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path="test.py", chunk_id=f"chunk_{edge_id}",
    )


def test_compute_node_idf():
    """IDF is higher for rare nodes than common ones."""
    builder = HypergraphBuilder()
    # 'hub' appears in 3 edges, 'rare' in 1
    builder.add_edge(_make_edge("e1", ["hub"], ["A", "rare"]))
    builder.add_edge(_make_edge("e2", ["hub"], ["B", "C"]))
    builder.add_edge(_make_edge("e3", ["hub"], ["D", "E"]))

    idf = builder.compute_node_idf()
    assert idf["hub"] < idf["rare"], (
        f"Hub idf ({idf['hub']:.2f}) should be lower than rare idf ({idf['rare']:.2f})"
    )
    # hub: log(1 + 3/3) = log(2) ≈ 0.69
    # rare: log(1 + 3/1) = log(4) ≈ 1.39
    assert abs(idf["hub"] - math.log(2)) < 0.01
    assert abs(idf["rare"] - math.log(4)) < 0.01


def test_get_hub_nodes_scales_with_graph_size():
    """Hub detection threshold adapts to graph size, not absolute degree."""
    builder = HypergraphBuilder()
    # Create 100 edges. 'hub' appears in all 100.
    # 'normal' appears in 2.
    for i in range(100):
        sources = ["hub", f"unique_{i}"]
        targets = [f"target_{i}"]
        if i < 2:
            sources.append("normal")
        builder.add_edge(_make_edge(f"e{i}", sources, targets))

    hubs = builder.get_hub_nodes(max_degree_pct=0.03)
    # 3% of 100 = 3. 'hub' has degree 100 → definitely a hub.
    assert "hub" in hubs
    # 'normal' has degree 2 → not a hub.
    assert "normal" not in hubs


def test_adjacent_edges_excludes_hub_nodes():
    """Hub nodes should not create adjacency connections."""
    builder = HypergraphBuilder()
    # e1 and e2 share 'hub' (which we'll exclude) and nothing else.
    # e1 and e3 share 'Session' (not a hub).
    builder.add_edge(_make_edge("e1", ["Session"], ["hub", "A"]))
    builder.add_edge(_make_edge("e2", ["hub"], ["B", "C"]))
    builder.add_edge(_make_edge("e3", ["Session"], ["D", "E"]))

    # Without filtering: e1 is adjacent to both e2 (via hub) and e3 (via Session)
    adj_unfiltered = builder.get_adjacent_edges("e1", s=1)
    adj_ids = {eid for eid, _ in adj_unfiltered}
    assert "e2" in adj_ids
    assert "e3" in adj_ids

    # With hub filtering: e1 is only adjacent to e3 (via Session)
    adj_filtered = builder.get_adjacent_edges("e1", s=1, exclude_nodes={"hub"})
    adj_ids_filtered = {eid for eid, _ in adj_filtered}
    assert "e2" not in adj_ids_filtered, "Hub-connected edge should be excluded"
    assert "e3" in adj_ids_filtered, "Non-hub connection should remain"


def test_idf_weighted_intersection_prefers_specific_nodes():
    """Intersection through specific nodes should score higher than through hubs."""
    builder = HypergraphBuilder()
    # 'int' appears in 20 edges (hub), 'Session' in 2 edges (specific)
    for i in range(20):
        builder.add_edge(_make_edge(f"e_int_{i}", ["int"], [f"x_{i}", f"y_{i}"]))
    builder.add_edge(_make_edge("e_sess_1", ["Session"], ["A", "B"]))
    builder.add_edge(_make_edge("e_sess_2", ["Session"], ["C", "D"]))

    idf = builder.compute_node_idf()
    # 22 total edges
    # int: log(1 + 22/20) = log(2.1) ≈ 0.74
    # Session: log(1 + 22/2) = log(12) ≈ 2.48
    assert idf["Session"] > 2 * idf["int"], (
        f"Session idf ({idf['Session']:.2f}) should be much higher than "
        f"int idf ({idf['int']:.2f})"
    )
```

### Update existing tests

The existing tests in `test_intersection.py` that call `retrieve()` should
still pass because hub filtering is computed automatically inside `retrieve()`.
Run all tests to verify:

```bash
uv run pytest tests/ -v
```

### Manual validation

After implementing, re-run both queries (no reindex needed):

```bash
# Keyword query — should still show Session/HTTPAdapter as top paths
uv run hce query "how does Session.send call HTTPAdapter" \
    --cache-dir ../requests/src/requests/.hce_cache -v

# NL query — Path 2 should no longer jump to utils.py via 'int'
uv run hce query "how does authentication work" \
    --cache-dir ../requests/src/requests/.hce_cache -v
```

Expected changes for the authentication query:
- Path 2 should stay within auth-related edges (not jump to is_valid_cidr,
  address_in_network, unquote_unreserved via `int`)
- Path 1 should be unchanged (auth.py DEFINES/INHERITS chain)
- `int`, `isinstance`, `getattr`, `len` should NOT appear as intersection
  nodes in the `↓ connected via: {}` lines

## Summary of files to change

| File | Change |
|------|--------|
| `graph/builder.py` | Add `compute_node_idf()`, `get_hub_nodes()`, modify `get_adjacent_edges()` |
| `retrieval/intersection.py` | Compute IDF+hubs in `retrieve()`, pass to adjacency calls, IDF-weight intersection scores, update `_build_traversal_paths` signature |
| `tests/test_hub_node_filtering.py` | New test file with 4 tests |

No changes needed to: `models.py`, `embeddings.py`, `context.py`, `pipeline.py`,
`mcp_server.py`, `cli.py`.

## Design Rationale

**Why 3% as the hub threshold?**
It's a balance. 1% is too aggressive — it would filter out meaningful nodes
in small graphs. 5% is too lenient — `int` at 8% of edges would slip through.
3% means: in a 500-edge graph, nodes in 15+ edges are hubs; in a 50,000-edge
monorepo, nodes in 1,500+ edges. The threshold could be made configurable
via the `retrieve()` function, but 3% is a robust default.

**Why both filtering AND IDF weighting?**
Belt and suspenders. Hub filtering (the 3% cutoff) is a hard gate: nodes above
the threshold are completely invisible to adjacency. IDF weighting is a soft
penalty: nodes below the threshold but still high-degree contribute less to
intersection scores. Together they handle both extreme hubs (filtered) and
moderate hubs (downweighted).

**Why not just use IDF without filtering?**
Because a node in 40% of edges would still create O(n) candidate edges in
`get_adjacent_edges`, even if its IDF is low. The filtering prevents the
combinatorial explosion in adjacency computation. IDF alone doesn't help
with performance — only with scoring.

**Why compute IDF at query time instead of caching at index time?**
IDF depends on the current graph state, which changes on incremental re-index.
Computing it from `_node_to_edges` is O(nodes) — fast enough that caching
adds complexity without meaningful benefit. For a 50,000-node graph, this is
a single pass over a dict. If profiling shows it's a bottleneck, it can be
cached in the builder and invalidated on add/remove.
