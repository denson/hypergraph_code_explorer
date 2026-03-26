# HCE Quickstart

## Install

HCE requires Python 3.11+ and git. Install from GitHub in one step:

```
pip install git+https://github.com/denson/hypergraph_code_explorer.git
```

If the repo is private and you need authentication:
```
pip install git+https://<GITHUB_TOKEN>@github.com/denson/hypergraph_code_explorer.git
```

Or clone first, then install in editable mode (useful for development):
```
git clone https://github.com/denson/hypergraph_code_explorer.git
pip install -e hypergraph_code_explorer
```

Verify it worked:
```
hce --help
```

### Troubleshooting: `hce` command not found

If `hce --help` fails with "command not found" or "'hce' is not recognized", the console
script wasn't added to PATH. This is common on Windows. Diagnose and fix:

```bash
# Step 1: Check if the script exists somewhere
# On Windows:
where hce 2>nul || python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
# On macOS/Linux:
which hce 2>/dev/null || python3 -c "import sysconfig; print(sysconfig.get_path('scripts'))"

# Step 2: If the script exists but isn't on PATH, use the module directly:
python -m hypergraph_code_explorer.cli --help

# Step 3: Create a shell alias for the session:
# On Windows (PowerShell):
function hce { python -m hypergraph_code_explorer.cli @args }
# On Windows (cmd):
doskey hce=python -m hypergraph_code_explorer.cli $*
# On macOS/Linux:
alias hce='python -m hypergraph_code_explorer.cli'
```

The `python -m hypergraph_code_explorer.cli` fallback is functionally identical to the
`hce` command. If you use it, substitute it for `hce` in all examples below.

**For the user**: If you want the `hce` command permanently available, add your Python
Scripts directory to your system PATH. Ask Claude to help you find and set the right path
for your OS.

### Keeping HCE updated

If you installed HCE via a Claude Code marketplace, enable auto-update so you always
get the latest version:

1. In Claude Code, run `/plugin`
2. Go to the **Marketplaces** tab
3. Select the HCE marketplace and toggle **Enable auto-update**

With auto-update enabled, Claude Code checks for new versions at startup and
updates automatically.

To manually update at any time:
```
/plugin marketplace update hce-tools
/reload-plugins
```

If updates aren't detected, force a refresh:
```
claude plugin update hce@hce-tools
```

As a last resort, clear the plugin cache and reinstall:
```bash
# Delete the cached plugin
rm -rf ~/.claude/plugins/cache
# On Windows: remove C:\Users\<you>\.claude\plugins\cache
# Then restart Claude Code — the plugin will re-download from the marketplace
```

Optional extras (append to any install command above):
```
pip install "hypergraph_code_explorer[embed]"   # Tier 4 semantic search
pip install "hypergraph_code_explorer[server]"  # MCP server mode
pip install "hypergraph_code_explorer[all]"     # Everything
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

### Probe the graph (single structural probe)
```bash
hce probe "what would break if I changed BaseEstimator.get_params"
hce probe "how does random forest handle missing values"
```

Each `hce probe` call classifies your question, runs structural queries, and builds a
memory tour from the results. A probe is ONE step in an investigation — for full
investigations, combine probes with targeted `hce lookup` and `hce search` calls.

### Investigation tours
```bash
# Start a tour — all subsequent lookup/search/probe results auto-append
hce tour start "My Investigation"
# → Started tour abc123: "My Investigation"

hce lookup SomeClass --callers --depth 2
# → Tour abc123: +5 steps (total: 5)

hce search "validation"
# → Tour abc123: +3 steps (total: 8)

# Quick lookup without polluting the tour
hce lookup NoisySymbol --no-tour

# Stop when done
hce tour stop
# → Stopped tour abc123: "My Investigation" (8 steps)

# Resume later
hce tour resume abc123
```

### Manage memory tours
```bash
hce tour list                                    # see all tours (active tour marked ◀)
hce tour show <id>                               # inspect a specific tour
hce tour annotate <id> --status weak --finding "Only text matches, no structural edges"
hce tour export --all --output investigation.json # save for later
hce tour import investigation.json               # resume a previous investigation
```

Tour status values: `active` (shown in visualization), `empty` (no results),
`weak` (low quality), `hidden` (excluded). Only `active` tours render in the visualization.

### Filtering test noise

On large codebases, test files can dominate results. Use `--no-tests` to filter them:

```bash
hce lookup Session --callers --no-tests
hce search "validate" --no-tests
hce probe "how does auth work" --no-tests
```

### Codebase overview
```bash
hce overview --top 20                # most-connected symbols (hub nodes)
```