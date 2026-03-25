# Agent Handoff — Memory Tours Validation and Graph Loading Discovery

## What happened

We indexed the FastAPI codebase with HCE, created memory tours from graph queries, validated the full tour lifecycle, and then tested whether an LLM agent can hold and traverse the entire raw graph in context. The answer: yes, easily.

## What was done

### 1. Indexed FastAPI

```bash
python -c "
from hypergraph_code_explorer.pipeline import HypergraphPipeline
from hypergraph_code_explorer.codemap import generate_codemap
p = HypergraphPipeline(verbose=True, skip_summaries=True)
stats = p.index_directory('target_repos/fastapi/fastapi')
generate_codemap(p.builder, cache_dir=p._cache_dir)
"
```

Result: **1,306 nodes, 1,083 edges, 48 files, 10 hub nodes.** Edge breakdown: 408 IMPORTS, 252 SIGNATURE, 242 CALLS, 89 INHERITS, 69 DEFINES, 23 RAISES. Index time ~10s. Cache at `target_repos/fastapi/fastapi/.hce_cache/`.

### 2. Created memory tours

Three auto-scaffolded tours via `HypergraphSession.memory_tour_create()`:

| Tour | Steps | Tags | Key symbols |
|------|-------|------|-------------|
| Routing System | 118 | routing | `routing.APIRouter`, `Dependant`, `serialize_response`, `WebSocket` |
| DI System | 9 | di | `DependencyCacheKey`, `SolvedDependency`, `add_non_field_param_to_dependency` |
| Validation | 22 | validation | `Request`, `oauth2.OAuth2.__call__`, `request_validation_exception_handler` |

Plus two middleware tours created during the end-to-end test:
- Middleware Pipeline (20 steps, auto-scaffolded)
- Middleware Stack (4 steps, simulated LLM-authored via `memory_tour_create_from_dict`)

All persisted to `target_repos/fastapi/fastapi/.hce_cache/memory_tours.json`.

### 3. Validated the full lifecycle

Tested: create, list, get (with usage tracking), promote, remove, scaffold prompt. All work through the `HypergraphSession` API. The Validation tour and the LLM-authored Middleware tour were promoted to durable memory.

### 4. Tested full graph loading

Dumped the entire graph as JSON: **520KB, 15,280 lines, 1,083 edges.** The agent read through ~25% of it and immediately built a correct mental model of the codebase architecture. The graph is structured data — uniform `{type, relation, sources, targets, file}` tuples — which LLMs traverse trivially.

## Key discovery: tours are bookmarks, not summaries

The original assumption was that tours need to be capped at 10-20 steps to be useful to agents. This is wrong. The 118-step Routing System tour was fully readable and traversable. So was the raw graph itself.

**For humans**, concise tours matter because visual and cognitive bandwidth is limited. **For LLM agents**, structured data like a hypergraph edge list is one of the easiest formats to process. A list of 1,000 `(source, relation, target, file)` tuples is simpler to parse than 1,000 lines of prose.

This means:
- **Tour step capping is unnecessary.** Don't limit `scaffold_from_plan` output.
- **Tours serve as bookmarks**, not reduced-size proxies for the graph. Their value is provenance (what query produced this), reuse tracking (how often is this recalled), and the promotion signal (is this working notes or established knowledge).
- **The full graph can be loaded directly.** A single `python -c` that dumps `_edge_store` as JSON gives an agent the entire structural blueprint.

## What still needs to change

These priorities were identified but not implemented:

### Priority 1: Task-oriented workflow recipes in the skill

`plugin/skills/hce-explore/SKILL.md` teaches exploration (stats → overview → search → lookup → query). It doesn't teach task workflows like:

- **Blast radius**: "I changed symbol X — what depends on it?" → `lookup(X, callers=True, depth=2)` → aggregate affected files → create tour
- **Bug tracing**: "Exception Y is raised here — where does bad data originate?" → trace callers backward from the raise site
- **Change review**: extract symbol names from a diff → lookup each one's callers → flag hub dependencies
- **Refactoring safety**: check callers + inheritors before moving/renaming

These are sequences of existing API calls with interpretation guidance. No new code needed.

### Priority 2: `symbols_in_file(path)` API method

An agent looking at a diff sees file paths. There's no API method to go from "this file changed" to "these are the graph nodes in that file." The builder has `source_path` on every edge, so the data exists — just needs a method on `HypergraphSession`.

### Priority 3: `blast_radius(symbol, depth)` API method

Convenience wrapper: lookup with `callers=True` + `inherits=True` backward, then aggregate into a structured report (affected files, symbols by depth, hub warnings).

### Priority 4: Fix `python -m` entry point

`__main__.py` exists as untracked. Wiring it up would give agents `python -m hypergraph_code_explorer` instead of `python -c "..."` one-liners that break in PowerShell.

### Priority 5: PowerShell compatibility in skill

The SKILL.md has bash-isms (`ls ... 2>/dev/null`, `||`, `&&`) that break in PowerShell. The escaping of f-strings with backslash-quotes inside `python -c` also fails in PowerShell (we hit this repeatedly during the tour test).

### Priority 6: Human-readable visualization for memory tours — IMPLEMENTED

The D3.js interactive viz in `skill/assets/viz_template.html` had its own tour format (`tours.json` with `{name, color, keywords, steps}`) that was separate from memory tours (`memory_tours.json` with `{name, tags, steps, annotations, provenance}`). These were two parallel systems that didn't talk to each other.

**This is now implemented.** The one-off `docs/generate_bug_viz.py` script was generalized into `src/hypergraph_code_explorer/visualization.py`, a reusable module that:
- Selects tours by tag or ID from the `MemoryTourStore`
- Extracts a focused subgraph (1-hop neighborhood of tour seed nodes)
- Converts memory tours to the viz template format (auto-assigned colors, symbol highlighting)
- Generates both a self-contained D3 HTML and a markdown report

The CLI gained `hce visualize`:
```
hce visualize --tags security,auth --output security_viz --title "Security"
```
This writes `security_viz.html` (interactive D3) and `security_viz.md` (markdown report) side-by-side. The `HypergraphSession` API also exposes `session.visualize()` for programmatic use. The viz `tours.json` format is retired — memory tours are now the single canonical tour format for both agent memory and human visualization.

## Bug Tour Visualization — from PDF to Interactive D3

### What happened

Ingested a GitHub issue PDF (FastAPI #13399: "Dependency Models created from Form input data are losing metadata and enforcing validation on default values"), traced the bug through the already-indexed FastAPI codebase, created two hand-crafted memory tours capturing the root cause and the JSON-vs-Form code path divergence, then generated a self-contained D3 force-directed graph visualization from those tours.

### The three-phase workflow

**Phase 1: PDF → Memory Tours.** Read the bug report PDF and correlated it with the indexed FastAPI source by reading `dependencies/utils.py`, `routing.py`, `params.py`, and `_compat/v2.py`. Identified the root cause: `_get_multidict_value` (lines 777-780 in `dependencies/utils.py`) pre-fills default values via `deepcopy(field.default)` for Form inputs before Pydantic sees the data, corrupting `model_fields_set`. JSON body handling never calls this function, so it works correctly. Created two hand-crafted tours following the `docs/memory-tours-guide.md` format and wrote them to `docs/bug_memory_tours.json`.

| Tour | Steps | Focus |
|------|-------|-------|
| Bug #13399: Form default prefill corrupts model_fields_set | 9 | Root cause trace from `params.Form` through `_extract_form_body` to `_get_multidict_value` |
| Bug #13399: JSON body vs Form body — why they differ | 8 | Side-by-side comparison of the two code paths showing where they diverge |

**Phase 2: Tours → Subgraph.** `docs/generate_bug_viz.py` loads `builder.pkl`, collects 24 seed nodes from tour steps and keywords, extracts all edges touching those seeds (1-hop neighborhood), computes degree and importance scores (with a boost for seed nodes to ensure visual prominence), and assigns groups by source file path (`utils`, `routing`, `params`, `compat`, etc.).

**Phase 3: Subgraph → HTML.** Converts the memory tour format to the viz tour format expected by the D3 template. The conversion wraps symbol names in `<strong class='tc'>...</strong>` tags so they pick up the tour color in the sidebar narrative. Assigns distinct colors to each tour (`#ff4466` for root cause, `#00d4ff` for code path comparison). Minifies graph data, injects it alongside tours and group color config into `skill/assets/viz_template.html`, and writes a single self-contained HTML file.

```
python docs/generate_bug_viz.py
```

### Output

- **320 nodes, 429 edges, 2 tours, 158 KB** self-contained HTML
- Both tours verified working: sidebar narrative with highlighted symbols, spotlight zoom on step click, keyboard arrow-key navigation, symbol search, colorblind mode
- All features from the existing viz template (breathing animation, drag, zoom, suggest tours, copy prompt) work unmodified

### Files produced

| File | Description |
|------|-------------|
| `docs/bug_memory_tours.json` | 2 memory tours, 17 steps total, tagged `bug-13399` |
| `docs/generate_bug_viz.py` | ~310-line one-off generator script |
| `docs/bug_13399_viz.html` | Self-contained D3 visualization (158 KB) |

### This is now the default visualization approach

The one-off `generate_bug_viz.py` has been generalized into `src/hypergraph_code_explorer/visualization.py` and the `hce visualize` CLI subcommand. The key design decisions that carried forward from the bug viz proof-of-concept:

1. **Memory tours as the unit of visualization.** Instead of visualizing everything, extract only the subgraph that a set of tours references. This produces focused, readable visualizations rather than overwhelming full-codebase dumps.
2. **Single-step pipeline.** `hce visualize` reads `builder.pkl` + `memory_tours.json` + `viz_template.html` and produces both a self-contained HTML and a markdown report. No intermediate files.
3. **Memory tour format is the single format.** The conversion is mechanical and automatic: step `node` → viz `node`, step `text` → viz `text` with `<strong class='tc'>` wrapping, auto-assigned colors from a 12-color palette, `keywords` derived from tour steps.
4. **Focused subgraphs are better for humans.** 199 nodes for 2 security tours vs. 1,275 nodes for all 8 tours vs. 1,306 in the full graph. The importance boosting for seed nodes ensures the tour-relevant symbols are visually prominent.
5. **Group colors are derived automatically.** No hardcoded `FILE_TO_GROUP` mapping — groups come from file path directory components with deterministic hash-to-hue coloring.

## Node Detail Panel — Design Exploration and Next Steps

### What happened

Prototyped three detail-panel layouts for the D3 visualization — clicking a node should open a panel showing that node's connections, tour references, and source code. Built three standalone HTML demos in `demo_viz/` using real data from the Bug #13399 viz (320 nodes, 429 edges, 2 tours). All three were browser-verified working.

### The three prototypes

| Demo | Layout | Interaction |
|------|--------|-------------|
| `demo_viz/right_drawer.html` | 400px panel slides in from right edge; graph container shrinks to accommodate | Close via X button or Escape; edge targets inside panel are clickable to navigate to other nodes |
| `demo_viz/bottom_sheet.html` | Panel slides up from bottom; S/M/L resize buttons (220px / 350px / 50vh); split pane with connections on left, source on right | Drag handle to resize; wide horizontal layout good for source code |
| `demo_viz/floating_card.html` | 370x480px card floats near clicked node; dashed SVG connector line from card to node; card is draggable by header | No layout shift; click another node to move card; compact (no source section) |

All three share a common data layer:
- `_data.js` — extracted `_raw`, `tours`, and `_groupColors` from `docs/bug_13399_viz.html`
- `buildDetailIndex()` — pre-computes per-node incoming/outgoing edge aggregations by type at page load
- `renderDetail(nodeId)` — produces HTML with metadata badges (group, importance, degree), connections grouped by type with direction arrows (`calls →` / `← called by`), tour references with color indicators, and a source placeholder

### Decision: Right Drawer

The right drawer is chosen for integration into the production `skill/assets/viz_template.html`. Rationale:

- Best balance of detail space without disrupting the left sidebar tour narrative
- Standard IDE-panel interaction pattern (familiar to developers)
- Scrollable — handles nodes with many connections without truncation
- Graph resize via CSS transition is smooth and non-jarring
- The left sidebar (tours, search, narrative) remains fully functional while the drawer is open

### Architecture patterns to carry forward

1. **Detail index at load time.** `buildDetailIndex()` iterates edges once and builds `{outgoing: {type: [{id, file}]}, incoming: {type: [{id, file}]}, tours: [...]}` per node. This is O(edges) and makes all subsequent lookups O(1).

2. **Async-ready interface.** Even though the current implementation reads from an inline JS object, the click handler should use `async getNodeDetail(nodeId)` so that a future server-backed mode (Option B from our design discussion) is a backend swap, not a template rewrite.

3. **Clickable edge targets.** Each connection badge in the panel is a clickable element with `data-node="full.node.id"` — clicking it calls `openDrawer(targetId)` to navigate to that node's detail without closing the panel.

4. **Escape and X to dismiss.** Escape key closes the drawer (or resets the view if no drawer is open). X button in the drawer header provides mouse-based dismissal.

### Next step: integrate into production

The implementation task is:

1. **Add drawer CSS and HTML** to `skill/assets/viz_template.html` — the `#detail-drawer` element, its styles, and the `body.drawer-open` transition rule for `#graph-container`.

2. **Add a `_details` injection point** to the template alongside the existing `{{DATA_INJECTION}}`, `{{TOURS_INJECTION}}`, and `{{CONFIG_INJECTION}}`. The `_details` object maps node IDs to their edge aggregations and chunk text.

3. **Update `src/hypergraph_code_explorer/visualization.py`** — in `extract_tour_subgraph()`, also build the detail index and include chunk text from `builder._chunk_registry`. Add a `_build_detail_data()` function and inject it via a new `{{DETAILS_INJECTION}}` placeholder.

4. **Add the drawer JS** to the template — `buildDetailIndex()`, `renderDetail()`, `openDrawer()`, `closeDrawer()`, and the `nodeSel.on('click', ...)` handler. The demo in `demo_viz/right_drawer.html` is the reference implementation.

5. **Wire chunk text into the source section.** Replace the current placeholder (`# Source chunk from builder._chunk_registry`) with actual code from `builder._chunk_registry[chunk_id]`. This requires passing chunk data through the detail injection.

### Files produced

| File | Description |
|------|-------------|
| `demo_viz/_data.js` | Extracted graph data constants from `docs/bug_13399_viz.html` (123 KB) |
| `demo_viz/right_drawer.html` | Right drawer prototype — **chosen for production** |
| `demo_viz/bottom_sheet.html` | Bottom sheet prototype with split pane and resize |
| `demo_viz/floating_card.html` | Floating card prototype with connector line |

## MCP is not the interface

The agent interface is shell commands (CLI or `python -c`), not MCP. The MCP server exists and exposes 6 tools, but the SKILL.md explicitly says to use the Python API via bash. Memory tours are not exposed via MCP and don't need to be — the `python -c` path works and is more reliable.

## Files touched

| File | Status |
|------|--------|
| `target_repos/fastapi/fastapi/.hce_cache/` | Created (index + tours) |
| `.gitignore` | Modified (added `target_repos/`) |
| `AGENT_HANDOFF.md` | Created (this document) |
| `docs/bug_memory_tours.json` | Created (2 bug tours from issue #13399 PDF) |
| `docs/generate_bug_viz.py` | Created (one-off viz generator, superseded by `hce visualize`) |
| `docs/bug_13399_viz.html` | Generated (self-contained D3 visualization) |
| `src/hypergraph_code_explorer/visualization.py` | Created (unified visualization module) |
| `src/hypergraph_code_explorer/cli.py` | Modified (added `hce visualize` subcommand) |
| `src/hypergraph_code_explorer/api.py` | Modified (added `HypergraphSession.visualize()`) |
| `demo_viz/_data.js` | Created (extracted graph data for demo prototypes) |
| `demo_viz/right_drawer.html` | Created (right drawer detail panel prototype — chosen for production) |
| `demo_viz/bottom_sheet.html` | Created (bottom sheet detail panel prototype) |
| `demo_viz/floating_card.html` | Created (floating card detail panel prototype) |

## Repo state

- `target_repos/` is gitignored — the FastAPI and Django repos are local test data
- The Django repo in `target_repos/django/` has not been indexed
- No commits were made during this session
