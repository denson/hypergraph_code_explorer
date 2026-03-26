---
name: hce
description: >
  Index any multi-language codebase into a hypergraph for structural analysis and
  interactive visualization using HCE. Use this skill whenever the user wants to:
  (1) visualize or map a codebase's architecture,
  (2) investigate a question about a codebase ("what would break if I changed X",
      "how does Y work", "what calls Z", "class hierarchy of W"),
  (3) do impact/blast-radius analysis,
  (4) trace dependencies, inheritance, or error handling patterns,
  (5) install or set up HCE.
  Triggers: "visualize this codebase", "show me the architecture", "graph this repo",
  "map dependencies", "explore codebase visually", "what would break if I changed",
  "how does X work in the code", "trace callers of", "class hierarchy",
  "blast radius", "investigate", "analyze this code", "install HCE",
  or any mention of "hypergraph", "code graph", "HCE", "D3 visualization".
  Use this skill even when the user asks a structural question about code without
  mentioning HCE — if there's an indexed codebase available, this skill can answer it.
---

# HCE — Hypergraph Code Explorer

HCE indexes a codebase into a hypergraph and lets you query or visualize its structure. It supports Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, and PHP — including mixed-language projects. This skill covers the full lifecycle: installation, indexing, querying, investigation, and interactive visualization.

## Which workflow?

This skill supports two primary workflows. Read the user's request and pick the right one:

- **"Visualize / map / explore this codebase"** → You want the visualization workflow.
  Read this entire file. It covers extraction, tour design, and D3.js visualization.

- **"How does X work?", "What would break if...", "What calls Y?", "Trace Z"** → You want
  the investigation workflow. Read `references/investigator.md` — it has the full methodology
  for decomposing questions into HCE queries, evaluating results, filtering noise, and
  synthesizing evidence-backed answers with memory tours.

- **"Install HCE" / "Index this repo"** → Read `references/quickstart.md` only.

If you're unsure, default to investigation for questions and visualization for exploration requests.
The two workflows share the same index — you can investigate a codebase and then visualize your
findings, or start with a visualization and drill into specific questions.

## Default behavior — auto-visualize

Unless the user explicitly says they only want to install, index, or query (no visualization), **always produce the full interactive visualization**. The visualization is the primary deliverable of this skill. The phases below flow automatically from install → index → extract → research → tours → visualization → present.

If the user opts out of visualization ("just index this", "I only want to query"), stop after the relevant phase.

## When to read what

- **User wants to install HCE or index a repo only** → Read `references/quickstart.md` and follow it. You don't need the rest of this file.
- **User wants a visualization (the default)** → Read this entire file. It covers extraction, querying the graph to understand the architecture, tour design, and the D3.js visualization.
- **An `.hce_cache/builder.pkl` already exists** → Skip to "Phase 2: Extract graph data" below.

## IMPORTANT: Use the `hce` CLI for all operations. Do NOT use MCP tools.

## Phase 1: Install and index

Read `references/quickstart.md` for the full quickstart. The short version:

```
# Install HCE from GitHub (one step, works on any OS)
pip install git+https://github.com/denson/hypergraph_code_explorer.git

# If the repo is private, use a token:
# pip install git+https://<GITHUB_TOKEN>@github.com/denson/hypergraph_code_explorer.git

# Verify — IMPORTANT: test this before proceeding
hce --help

# If "hce" is not found, use the module fallback (functionally identical):
python -m hypergraph_code_explorer.cli --help
# See references/quickstart.md "Troubleshooting" section for full diagnosis and fixes.

# Index a codebase — point at the source root
hce index ./my-project/src --skip-summaries --verbose

# Check the index
hce stats --cache-dir ./my-project/src/.hce_cache
```

The source root is the directory containing the actual source code. For projects with nested structure, point at the package directory, not the repo root. Check `pyproject.toml`, `package.json`, `go.mod`, or `Cargo.toml` if you're unsure.

HCE uses tree-sitter for multi-language extraction. It handles Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, and PHP — including projects that mix several of these. Files in unsupported languages are indexed with a lightweight regex fallback that captures definitions and imports.

Always use `--skip-summaries` to keep the pipeline zero-cost (no API key needed). The index saves to `.hce_cache/` inside the source root and persists across sessions.

### HCE query commands

Once indexed, HCE has four query commands. These are the primary way to understand a codebase — use them before reading any source files, both for standalone exploration and during visualization tour design (see Phase 3):

```bash
hce lookup FastAPI --calls           # what does this class call?
hce search "middleware"              # find symbols by name
hce query "how does auth work"       # natural language
hce overview --top 20                # key symbols by degree
```

All commands accept `--cache-dir <path>` and `--json` for structured output.

## Phase 2: Extract graph data

Extract the graph from the pickle:

```bash
python <skill-dir>/scripts/extract_graph.py <path-to-.hce_cache> /tmp/graph_data.json
```

The bundled `scripts/extract_graph.py` handles the EdgeType pickle compatibility automatically (it mocks the enum with a flexible metaclass so pickle doesn't choke on custom edge types).

Output is a JSON file with:
- `nodes`: `[{id, label, group, degree, importance, language}, ...]`
- `edges`: `[{source, target, type, file}, ...]`

Each node includes a `language` field derived from its source file extension (e.g., `"python"`, `"javascript"`, `"go"`). This enables the visualization to color or filter by language — especially useful for mixed-language codebases where you want to see cross-language call patterns.

The importance formula: `importance = 2 × (calls_degree + inherits_degree) + total_degree`. This weights structural centrality — nodes involved in many calls or inheritance chains matter more than nodes with only DEFINES edges.

Only structural edge types are included: DEFINES, CALLS, INHERITS, DECORATES, RAISES. IMPORTS and SIGNATURE are excluded — they add noise without visual value.

### Customizing groups

The extraction script assigns groups based on the top-level module in the node ID. For a better visualization, customize groups per codebase. Examine the extracted data to identify natural clusters, then define a prefix mapping. For example, for FastAPI:

```python
prefix_groups = {
    "applications.": "app",
    "routing.": "routing",
    "utils.get_openapi": "openapi",
    "utils.solve": "dependencies",
    "utils.analyze": "dependencies",
    "Security": "security",
    "OAuth": "security",
    "exception": "exceptions",
    "HTTPException": "exceptions",
}
```

You can either modify the script or do group assignment in a separate Python step after extraction.

## Phase 3: Research the codebase via HCE queries

Before designing tours, use HCE's query commands to understand the codebase architecture. This is the token-efficient path — HCE returns compact, structured answers about symbol relationships without you reading raw source files.

Start broad, then narrow:

```bash
# 1. Get the big picture — top symbols by structural centrality
hce overview --top 30 --json --cache-dir <path-to-.hce_cache>

# 2. Identify subsystems from the overview output — look for clusters
#    of related symbols (routing.*, security.*, dependencies.*, etc.)

# 3. For each candidate subsystem, search for its symbols
hce search "routing" --json --cache-dir <path-to-.hce_cache>
hce search "middleware" --json --cache-dir <path-to-.hce_cache>

# 4. Trace key symbols to understand how subsystems connect
hce lookup get_request_handler --calls --depth 2 --json --cache-dir <path-to-.hce_cache>
hce lookup FastAPI --calls --json --cache-dir <path-to-.hce_cache>

# 5. Ask architectural questions in natural language
hce query "how does dependency injection work" --cache-dir <path-to-.hce_cache>
hce query "what calls the request validation pipeline" --cache-dir <path-to-.hce_cache>
```

The `--json` flag gives structured output you can parse; omit it for human-readable summaries. All commands accept `--cache-dir` if you're not running from the source root.

Only read source files when the query results aren't enough to write a specific tour narrative — for example, when you need to explain *why* a function exists or *what would break* without it and the graph structure alone doesn't make that clear. Even then, read only the specific function, not the whole file.

This matters because for a codebase like Django (23,000+ nodes, 1,163 files), reading source files to understand architecture would burn through the context window. The graph index already has the structural picture — use it.

## Phase 3b: Design guided tours

With the query results in hand, design 5-8 tours. Each tour covers a subsystem or architectural concern and needs:

- **name**: Human-readable title (e.g., "Routing & Endpoints")
- **colorGroup**: Which group color to use for highlighting
- **keywords**: Node ID prefixes that belong to this tour. The visualization matches nodes whose ID equals or starts with any keyword.
- **steps**: 4-6 narrative steps, each with explanatory text and a target node ID

Always include a **"Most Important"** tour highlighting the top ~15-20 highest-importance nodes across all subsystems. These are the "load-bearing walls" of the codebase — the symbols that would cause the most breakage if removed.

### Writing tour narratives

The narratives are what make the visualization genuinely useful. Each step should:

- Name the symbol in a `<strong class='tc'>` tag (dynamically colored to match the tour)
- Explain *why it exists* and *what would break without it* in 1-2 sentences
- Reference connected symbols to show how the piece fits the larger picture

```javascript
{text:"<strong class='tc'>get_request_handler</strong> builds each route's handler by wiring dependency solving, validation, and serialisation together.", node:"routing.get_request_handler"}
```

For the "Most Important" tour, include the importance score in the narrative so users understand the ranking:

```javascript
{text:"<strong class='tc'>routing.app</strong> (importance 252) — the ASGI app closure generated for every route. The single most connected symbol in the entire codebase.", node:"routing.app"}
```

## Phase 4: Generate the visualization HTML

The visualization is built from a bundled template (`assets/viz_template.html`) plus a generator script. You don't need to write D3.js code — just prepare the tour data and run the script.

### Step 1: Create a tours.json file

This is the creative part — your job is to turn the Phase 3 research into a structured JSON file that the generator consumes. The format:

```json
{
  "title": "FastAPI Architecture",
  "group_colors": {
    "routing": "#00d4ff",
    "app": "#7c5cff",
    "dependencies": "#ff8c42",
    "security": "#ff6eb4",
    "openapi": "#00e88f",
    "exceptions": "#ff4466",
    "other": "#555570"
  },
  "group_colors_cb": {
    "routing": "#56B4E9",
    "app": "#F0E442",
    "dependencies": "#E69F00",
    "security": "#CC79A7",
    "openapi": "#009E73",
    "exceptions": "#D55E00",
    "other": "#666688"
  },
  "tours": [
    {
      "name": "Routing & Endpoints",
      "color": "#00d4ff",
      "keywords": ["routing.", "APIRoute"],
      "steps": [
        {"text": "<strong class='tc'>get_request_handler</strong> builds each route's handler.", "node": "routing.get_request_handler"},
        {"text": "<strong class='tc'>APIRoute</strong> wraps a path + endpoint into a callable.", "node": "routing.APIRoute"}
      ]
    }
  ]
}
```

**group_colors** maps the group names from `extract_graph.py` output to hex colors. Choose vibrant colors on a dark background — warm for "hot" code paths, cool for infrastructure. Include a colorblind-safe set in **group_colors_cb** using the Wong (2011) palette.

Each tour needs a **name**, a **color** for highlighting, **keywords** (node ID prefixes that belong to this tour), and **steps** with HTML narrative text and a target node ID.

Always include a **"Most Important"** tour highlighting the top 15-20 highest-importance nodes. For this tour, include the importance score in the narrative.

### Step 2: Run the generator

```bash
python <skill-dir>/scripts/generate_viz.py /tmp/graph_data.json /tmp/tours.json /tmp/viz_output.html
```

Optional: `--title "My Project Architecture"` overrides the title from tours.json.

The script reads the template from `assets/viz_template.html`, minifies the graph data, injects everything, and writes a single self-contained HTML file. No external dependencies at runtime — the D3.js CDN link is the only external resource.

### What the template includes

The bundled template handles all the visualization mechanics. You don't need to write any of this, but understanding what's built in helps you design better tours:

- **Force-directed layout** with D3.js — DEFINES edges short/strong, INHERITS medium, CALLS longer/weaker
- **Importance-based rendering** — high-importance nodes are large with glow, low-importance nodes are tiny with blur ("cloud" effect)
- **Dark halo labels** — readable against any background via SVG `paint-order: stroke`
- **Tour system** — clicking a tour highlights its subgraph, shows narrative steps in the sidebar, auto-zooms to fit
- **Click-to-spotlight** — clicking a narrative step zooms to the target node, enlarges it, dims everything else, shows a pulsing ring
- **Live breathing** — sinusoidal wobble keeps the graph alive; Freeze/Live toggle
- **Dual palettes** — default vibrant + colorblind-safe, toggled via button
- **Language mode** — for 