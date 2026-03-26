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
- **An `.hce_cache/builder.pkl` already exists** → Skip to "Phase 2: Research the codebase" below.

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

**Plugin users**: If installed via Claude Code marketplace, enable auto-update (see `references/quickstart.md` "Keeping HCE updated") to get new versions automatically.

### HCE query commands

Once indexed, HCE has several query commands. These are the primary way to understand a codebase — use them before reading any source files, both for standalone exploration and during visualization tour design (see Phase 3):

```bash
hce lookup FastAPI --calls           # what does this class call?
hce lookup FastAPI --callers         # what calls this class?
hce search "middleware"              # find symbols by name
hce query "how does auth work"       # natural language
hce probe "what validates input"     # single structural probe (prints summary)
hce blast-radius Client.send         # impact analysis
hce overview --top 20                # key symbols by degree
```

All commands accept `--cache-dir <path>`, `--json`, and `--no-tests` (filters out test/benchmark/example file noise).

## Phase 2: Research the codebase via HCE queries

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

## Phase 2b: Build tours from your investigation

Tours accumulate automatically as you run queries. The primary workflow:

```bash
# Start a tour — all subsequent lookup/search/probe results auto-append
hce tour start "Routing Architecture"
# → Started tour abc123: "Routing Architecture"

hce lookup get_request_handler --calls --depth 2
# → Tour abc123: +8 steps (total: 8)

hce search "middleware"
# → Tour abc123: +3 steps (total: 11)

hce probe "how does dependency injection work"
# → Tour abc123: +12 steps, skipped 5 duplicates (total: 23)

# Stop when you have enough evidence
hce tour stop
```

For a full visualization, build 3-8 tours covering different subsystems or concerns. Always include a broad tour for the most-connected symbols:

```bash
hce tour start "Hub Symbols"
hce overview --top 20
hce tour stop
```

### Advanced: Manual tours.json

You can also provide a hand-crafted `tours.json` file for full control over tour narratives:

```bash
hce visualize --tours tours.json --output my_viz
```

Each tour in the JSON needs a **name**, **keywords** (node ID prefixes), and **steps** with HTML narrative text and a target node ID. See the investigation workflow above for the recommended approach.

## Phase 3: Generate the visualization

Run a single command — it extracts graph data, computes a force layout in Python (numpy-based grid repulsion), and generates a self-contained HTML file:

```bash
hce visualize --output my_project
# Writes my_project.html and optionally my_project.md
```

All active tours are included automatically. Options:

```bash
hce visualize --output my_project --title "FastAPI Architecture"
hce visualize --tours tour1_id,tour2_id --output my_project  # specific tours only
hce visualize --max-svg 500 --output my_project              # limit SVG node count
```

### What the visualization includes

The bundled template handles all the visualization mechanics:

- **Precomputed force layout** — layout is computed in Python, D3.js renders only
- **Importance-based rendering** — high-importance nodes are large with glow, low-importance nodes are tiny with blur ("cloud" effect)
- **Dark halo labels** — readable against any background via SVG `paint-order: stroke`
- **Tour system** — clicking a tour highlights its subgraph, shows narrative steps in the sidebar, auto-zooms to fit
- **Click-to-spotlight** — clicking a narrative step zooms to the target node, enlarges it, dims everything else, shows a pulsing ring
- **Fog toggle** — Fog On/Off button controls visibility falloff from tour nodes
- **Search** — type a node name and press Enter to find it
- **Dual palettes** — default vibrant + colorblind-safe, toggled via button
- **Tour deduplication** — tours with the same name are merged automatically