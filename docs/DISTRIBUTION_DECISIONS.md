# HCE Distribution: Open Questions and Decisions

Last updated: 2026-03-23

## Resolved

### Python API is the universal interface
- **Decision**: All environments use the Python API via `python -c "..."` commands
- **Rationale**: The `hce` CLI has PATH issues on Windows (pip installs `hce.exe` to a Scripts directory not on Git Bash's PATH). `python -m hypergraph_code_explorer` fails (no `__main__`). The Python API works identically on Windows, Mac, and Linux with no PATH configuration.
- **Implication**: The skill teaches Claude to use `python -c "from hypergraph_code_explorer.api import HypergraphSession; ..."` — never the CLI, never `python -m`.

### MCP server is unnecessary
- **Decision**: Drop the MCP server entirely
- **Rationale**: We proved the Python API works in both Cowork (via Bash tool) and Claude Code (via Bash). The MCP server added complexity (process management, startup script, `.mcp.json` config) without adding capability. Claude is also better at writing CLI/Python commands than calling MCP tools.
- **Status**: The MCP server code still exists in `mcp_server.py` and `start_server.py` for anyone who wants to use it directly, but the skill and plugin don't rely on it.

### Multi-repo support via session registry
- **Decision**: The MCP server (if used) maintains a dict of `HypergraphSession` objects keyed by source path. The Python API approach uses separate session objects per repo.
- **Rationale**: Users exploring multiple codebases in one session need to switch between them. Loading from `.hce_cache` is instant so switching is cheap.

### Cache reuse over re-indexing
- **Decision**: Always check for `.hce_cache/` before indexing. If it exists, load from cache. Only re-index if the user explicitly asks.
- **Rationale**: Indexing is fast but not free (Django takes ~3 minutes). Caches persist on disk and don't go stale often.

### Repo is private, collaborator-based sharing
- **Decision**: Keep the repo private. Share access by adding GitHub collaborators.
- **Rationale**: Not ready for public distribution. The `pip install` from GitHub works for anyone with repo access and git credentials configured.
- **License**: MIT (already added).

### Plugin marketplace works for Claude Code CLI
- **Decision**: The repo itself is the marketplace (`.claude-plugin/marketplace.json` at repo root). Install via:
  ```
  /plugin marketplace add denson/hypergraph_code_explorer
  /plugin install hce@hce-tools
  ```
- **Scope options**: User (global), Project (all collaborators), Local (just you, just this repo).

### Cowork installs via .plugin file
- **Decision**: Share the `.plugin` zip file directly. Install via Customize → Personal plugins → +.
- **Note**: The Cowork marketplace UI ("Add marketplace") fails with private repos — it can't authenticate to GitHub from the desktop app UI.

## Open Questions

### Do we still need the plugin at all?
- The plugin currently bundles: `.mcp.json` (MCP server config), `scripts/start_server.py`, skill, README.
- If we drop MCP, the plugin is just a skill with packaging overhead.
- A bare skill (just the `skill/` directory) might be simpler to distribute.
- **But**: The plugin marketplace is the established distribution path for Claude Code. A skill alone has no standard install mechanism in Claude Code.
- **Question**: Is there a way to distribute a skill-only package without the plugin wrapper?

### How does the Cowork plugin relate to Claude Code?
- Installing a plugin in Cowork does NOT make it available in Claude Code (they have separate plugin systems).
- However, the skill from a Cowork plugin DOES appear in Claude Code sessions launched from the same desktop app (we observed this — `hce:hce-explore` showed up in both).
- **Question**: Is the skill sharing a reliable feature or an accident? Can we depend on it?

### How should the skill be updated?
- Currently: rebuild `.plugin` file, redistribute, user reinstalls.
- For Claude Code CLI: `marketplace update` + `plugin install` pulls from GitHub.
- For Cowork: manual reinstall of `.plugin` file.
- **Question**: Is there a way to auto-update skills in Cowork without manual reinstall?

### Should we remove the MCP infrastructure from the repo?
- Files in question: `mcp_server.py`, `start_server.py`, `.mcp.json`, `plugin/scripts/`
- The MCP server still works and someone might want it for direct integration.
- But keeping dead code in the distributed plugin adds confusion.
- **Question**: Remove from plugin but keep in source? Or remove entirely?

### What about the old `skill/` directory?
- The repo has `skill/` (the original hce-visualize skill) and `plugin/skills/hce-explore/` (the new skill).
- These have different content — the old one is about visualization, the new one is about code exploration via the Python API.
- **Question**: Should we merge them? Replace the old one? Keep both?

### Installation in fresh environments
- `pip install "hypergraph-code-explorer @ git+https://github.com/denson/hypergraph_code_explorer.git"` requires git credentials for the private repo.
- In Cowork VM, pip install works because the VM has network access. But does it have git credentials? (It worked in this session — HCE was already installed. Would it work from scratch?)
- **Question**: Test a fully clean Cowork session where HCE is NOT pre-installed. Does `pip install` from GitHub work in the VM?

### What happens when the repo goes public?
- The `pip install` URL stays the same but no longer needs credentials.
- The marketplace URL stays the same.
- The `.plugin` file stays the same.
- **Question**: Is there anything else that needs to change?

### README is out of date
- Still references "plugin with MCP server" as the installation method.
- Still has the old skill installation instructions (copy to `~/.claude/skills/`).
- **Action needed**: Rewrite to reflect current architecture (skill-based, Python API, no MCP dependency).

### Cowork VM is ephemeral — pip installs don't persist
- The Cowork VM resets between sessions. `pip install` puts HCE in the VM's Python, which is gone next session.
- Files written to mounted folders (e.g., `.hce_cache/` inside a repo) DO persist on the user's disk.
- **Implication**: Every new Cowork session has a cold-start cost: `pip install` HCE (~30 seconds), then load caches.
- **Possible mitigation**: Pre-index repos using Claude Code (app or terminal). The caches persist on disk. Then Cowork only needs to install HCE and load caches — no indexing step.
- **Question**: Is there a way to pre-install Python packages in the Cowork VM? Or cache pip packages across sessions?
- **Question**: Should the skill handle this transparently (detect "not installed," install, continue) or should there be a separate "setup" step the user runs first?

### Cowork can edit repo files but can't permanently install software
- Cowork can write `.hce_cache/` to repos in mounted folders — these persist.
- Cowork can create/edit files in the workspace — these persist.
- Cowork CANNOT permanently install pip packages — these are lost when the session ends.
- **Question**: Does this mean Cowork should always be paired with Claude Code for the initial setup? Claude Code (running on the real OS) does `pip install` permanently, indexes repos, creates caches. Then Cowork loads caches using a fresh `pip install` each session.

### Should Claude Code in the desktop app pre-index repos for Cowork?
- Claude Code in the desktop app has the same skill and can run `pip install` + indexing on the real OS.
- The pip install persists (it's on the real machine, not a VM).
- The caches persist (they're in the repo directory).
- **Possible workflow**:
  1. User installs plugin in Claude Code (one-time)
  2. Claude Code indexes repos (caches persist on disk)
  3. User opens Cowork — skill detects caches, installs HCE in VM, loads and queries
- **Question**: Is this the right recommended workflow? Or is it too many steps?

### What if HCE was vendored into the plugin/skill?
- Instead of `pip install` from GitHub, the skill could include the HCE source directly.
- Cowork could add the vendored source to `sys.path` and import directly — no pip install needed.
- This would eliminate the cold-start cost and the GitHub authentication requirement.
- **Trade-off**: The plugin/skill becomes much larger, and updates require redistributing the whole bundle.
- **Question**: Is this worth exploring? What's the HCE package size without dependencies?
