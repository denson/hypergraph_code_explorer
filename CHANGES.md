# Changes: Unified visualization — memory tours as the single viz format

Retired the separate viz tour format (`tours.json`) and made memory tours the single canonical format for both agent memory and human-readable visualization. The one-off `docs/generate_bug_viz.py` script that proved the approach has been generalized into a reusable module.

## New files

### `src/hypergraph_code_explorer/visualization.py`
Core visualization module consolidating logic from `docs/generate_bug_viz.py` and `skill/scripts/generate_viz.py`:
- `select_tours()` — filter tours from a `MemoryTourStore` by tag or ID
- `extract_tour_subgraph()` — 1-hop neighborhood extraction from tour seed nodes with importance scoring, seed-node boosting, and auto-assigned groups from file paths
- `memory_tours_to_viz()` — converts `MemoryTour` objects to the D3 template format with auto-assigned colors from a 12-color palette and symbol highlighting via `<strong class='tc'>` tags
- `generate_html()` — end-to-end HTML generation (subgraph + tour conversion + template injection)
- `generate_report()` — markdown report with tour index table, per-tour step listings, tag distribution, type breakdown
- `generate_visualization()` — top-level orchestrator writing both `.html` and `.md`

## Modified files

### `src/hypergraph_code_explorer/cli.py`
- Added `hce visualize` subcommand: `hce visualize [--tags t1,t2] [--tours id1,id2] [--output basename] [--title "Title"] [--cache-dir path]`
- Writes `<basename>.html` (interactive D3) and `<basename>.md` (markdown report) side-by-side

### `src/hypergraph_code_explorer/api.py`
- Added `HypergraphSession.visualize()` method wrapping the visualization module for programmatic use

## Design decisions

- **Tour-focused subgraphs by default.** Only nodes/edges referenced by selected tours (+ 1-hop neighborhood) are included. Tested: 2 security tours → 199 nodes; all 8 FastAPI tours → 1,275 nodes; full graph = 1,306.
- **Both outputs by default.** Interactive D3 HTML for exploration, markdown report for text scanning — same command produces both.
- **Color assignment is automatic.** Tours get colors from a palette indexed by position. Group colors derived from file path directory components via deterministic hash-to-hue mapping.
- **The D3 template is unchanged.** `viz_template.html` is format-agnostic once data is injected.

## Superseded (not deleted)

- `docs/generate_bug_viz.py` — one-off script, kept for reference
- `skill/scripts/generate_viz.py` — old generic generator, kept for backward compat

---

# Changes: Memory Tours — persistent agent-facing architectural memory

Agent-facing "memory tours" that capture useful graph query results as persistent, reusable architectural notes. Memory tours access the full graph (all edge types including IMPORTS and SIGNATURE), unlike visualization tours which filter to structural-only. Tours are ephemeral by default and can be promoted to durable memory.

## New files

### `src/hypergraph_code_explorer/memory_tours.py`
Data model, sidecar persistence, and scaffold functions:
- `MemoryTourStep` / `MemoryTour` — dataclasses with provenance (query, timestamps, tags, promoted flag, use_count) and full `to_dict`/`from_dict` round-tripping
- `MemoryTourStore` — file-backed CRUD store persisting to `.hce_cache/memory_tours.json`
- `scaffold_from_plan()` — derives a memory tour from any `RetrievalPlan` result
- `scaffold_prompt()` — produces a structured LLM prompt for richer agent-authored tours

### `tests/test_memory_tours.py`
23 tests covering data model, sidecar persistence, scaffolding, and filtering.

## Modified files

### `src/hypergraph_code_explorer/api.py`
- `HypergraphSession.__init__` now accepts optional `cache_dir`; `load()` passes the cache directory through
- Added 7 memory tour methods: `memory_tour_create`, `memory_tour_create_from_dict`, `memory_tour_list`, `memory_tour_get`, `memory_tour_promote`, `memory_tour_remove`, `memory_tour_scaffold_prompt`
- Lazy `MemoryTourStore` initialization via `_get_tour_store()`

### `src/hypergraph_code_explorer/cli.py`
- Added `hce tour` subcommand group with 6 sub-subcommands: `list`, `show`, `create`, `promote`, `remove`, `scaffold`
- Refactored cache-dir resolution into shared `_resolve_cache_dir()` helper used by both builder loading and memory tour loading

## How it works

1. `hce tour create "how does auth work"` runs `dispatch()`, scaffolds a `MemoryTour` from the `RetrievalPlan`, and persists it to `.hce_cache/memory_tours.json`
2. `hce tour list` / `hce tour show <id>` recall saved tours
3. `hce tour promote <id>` marks a tour as durable memory
4. `hce tour scaffold "query"` emits a structured LLM prompt for richer tour authoring
5. The `HypergraphSession` API exposes the same operations for programmatic / MCP use

---

# Changes: hce-visualize skill overhaul + multi-language visualization

This document summarizes the changes made to the `hypergraph_code_explorer` repo in this session. Use it as context when working on follow-up tasks.

## What changed and why

HCE recently gained tree-sitter-based multi-language extraction (10 languages: Python, JS, TS, Go, Rust, Java, C, C++, Ruby, PHP). The skill and visualization infrastructure needed to catch up. The goals were:

1. Make visualization the **default behavior** (auto-visualize unless the user opts out)
2. Add **language-aware coloring** so mixed-language codebases are visually distinguishable
3. Bundle a **reusable D3.js template** so the agent doesn't write 200+ lines of D3 from scratch each run
4. Add **search suggestions**, **tour suggestions**, and a **copy-prompt-to-clipboard** feature to the visualization UI
5. Move the skill into the HCE repo itself (at `skill/`)
6. Rewrite the README for a non-programmer audience while keeping developer docs intact

## New files

### `skill/assets/viz_template.html` (~580 lines)
Complete self-contained D3.js visualization template. The agent never writes D3 code — it writes a `tours.json` and runs `generate_viz.py`, which injects data into this template.

Key features baked into the template:
- Force-directed layout with importance-based node sizing
- Dark halo labels, breathing animation, zoom/pan
- **Tour system** with click-to-spotlight and step navigation
- **Dual color modes**: "color by module" (default) and "color by language" (toggle button + legend). Language colors use the GitHub palette.
- **Dual palettes**: default + colorblind-safe, toggled in-UI
- **Symbol search** with suggestion chips (top 6 highest-importance nodes shown below the search box)
- **"Suggest Tours"** button: client-side cluster analysis that finds high-importance clusters, cross-language boundaries, and structural patterns worth exploring
- **"Copy Prompt for Claude"** button: generates a Claude-ready prompt pre-loaded with graph context (top symbols, groups, languages), copies to clipboard so the user can paste it into a Claude conversation to request richer AI-generated tours
- `[?]` help toggle explaining how to add more tours
- Keyboard navigation (Escape to deselect, arrow keys in tour steps)

Placeholder markers for injection:
- `{{TITLE}}` — project name
- `// {{DATA_INJECTION}}` — minified graph data
- `// {{TOURS_INJECTION}}` — tour definitions + group colors
- `// {{CONFIG_INJECTION}}` — optional config overrides

### `skill/scripts/generate_viz.py`
Takes `graph.json` + `tours.json` → reads the template → injects data → writes a self-contained HTML file.

- Minifies graph data using short keys: `{n:[{i,l,g,d,p,ln}], e:[{s,t,y,f}]}`
- Resolves the template path relative to its own location: `script_dir.parent / "assets" / "viz_template.html"`
- CLI: `python generate_viz.py <graph.json> <tours.json> <output.html> [--title "Title"]`

The `tours.json` format:
```json
{
  "title": "Project Name",
  "group_colors": {"routing": "#00d4ff"},
  "group_colors_cb": {"routing": "#56B4E9"},
  "tours": [
    {
      "name": "Tour Name",
      "color": "#hex",
      "keywords": ["prefix_match"],
      "steps": [{"text": "HTML explanation", "node": "symbol_id"}]
    }
  ]
}
```

## Modified files

### `skill/scripts/extract_graph.py`
- Added `EXT_TO_LANGUAGE` mapping covering all 10 languages plus variants (.jsx, .tsx, .mjs, .hpp, etc.)
- Added `detect_language(file_path)` function
- Each node now includes a `"language"` field derived from its source file extension
- Output format: `{nodes: [{id, label, group, degree, importance, language}], edges: [{source, target, type, file}]}`

### `skill/SKILL.md`
- Frontmatter description trimmed to 4 lines (was ~14) to fit `.skill` package length limits
- Added "Default behavior — auto-visualize" section making visualization opt-out rather than opt-in
- Phase 1 updated for multi-language source root guidance
- Phase 2 updated: nodes now carry a `language` field
- Phase 4 completely rewritten: instead of inline D3 code examples, it now says "create tours.json and run generate_viz.py"
- pip install path updated to `"$(dirname "<skill-dir>")"` for co-located repo
- Final line count: ~255 (down from 378)

### `skill/references/quickstart.md`
- Removed "Python only" limitation
- Install instructions updated for co-located repo: `cd "$(dirname "<skill-dir>")"`
- Source root examples expanded for Python, Node.js, Go, Rust, Java
- Limitations section rewritten: 10 tree-sitter languages + regex fallback

### `README.md`
Major rewrite for accessibility:
- New lead paragraph: "Turn any codebase into an interactive visual map — for humans and AI agents alike"
- Mentions both human visualization and AI token savings
- Added visualization screenshot reference: `hypergraph_code_visualization.png`
- New "Why Hypergraphs? Why AST?" section explaining tree-sitter vs LLM-based approach, crediting [HyperGraphReasoning](https://github.com/lamm-mit/HyperGraphReasoning) as the inspiration project
- "Install the Skill" section split into Option A (Cowork — no coding required) and Option B (Claude Code — terminal)
- "What happens when you use it" — 5-step plain-language explanation of the auto-visualization flow
- "For Developers" divider cleanly separating technical content below
- Removed "Additional Skills" section (the old `skills/hce-index/` directory was deleted)

## Deleted

### `skills/hce-index/` (entire directory)
Old Python-only indexing skill. Predated tree-sitter, said "Python AST", had no visualization. Everything it did is now covered by `skill/` (hce-visualize), which handles indexing, querying, *and* visualization — plus supports 10 languages.

## Still exists but should be cleaned up (outside this repo)

- `hypergraphs/hce-visualize/` — the original skill folder before it was moved into the repo at `skill/`. Can be deleted.
- `hypergraphs/hce-visualize.skill` — old packaged skill file. Can be deleted or regenerated from the new `skill/` directory.

## How the visualization pipeline works now

1. Agent runs `hce index <source-root> --skip-summaries` → produces `.hce_cache/`
2. Agent runs `extract_graph.py` → reads `.hce_cache/`, produces `graph.json` with language-tagged nodes
3. Agent researches the codebase using `hce lookup`, `hce overview`, etc.
4. Agent writes `tours.json` with guided tours, group colors, and colorblind-safe palette
5. Agent runs `generate_viz.py graph.json tours.json output.html` → injects data into the bundled template → outputs a self-contained HTML file
6. User opens the HTML file in any browser
