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
