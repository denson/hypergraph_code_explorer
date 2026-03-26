"""
Init Command
=============
Generates tool-specific instruction files (CLAUDE.md, .cursorrules, AGENTS.md)
that teach AI agents how to use hce for code intelligence.
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# Instruction templates
# ---------------------------------------------------------------------------

CLAUDE_CODE_INSTRUCTIONS = """\
## Code Intelligence

This project is indexed by Hypergraph Code Explorer (hce). A structural
map is at `.hce/CODEBASE_MAP.md` — read it first when you need to
understand the project or find where something lives.

For deeper queries, use the CLI (available as `hce` in this project):

### Quick Reference
  hce lookup <symbol>              # where is it defined? what edges touch it?
  hce lookup <symbol> --callers    # what calls it? (reverse call graph)
  hce lookup <symbol> --inherits   # class hierarchy
  hce lookup <symbol> --depth 2    # follow relationships 2 hops
  hce search <term>                # text search across all symbols
  hce query "natural language"     # full dispatch query
  hce overview                     # module summary + hub nodes

### Probe — Single Structural Probe
  hce probe "your question here"

  Use `probe` to run a single structural query against the graph. It
  decomposes your question into structural queries, builds a guided
  tour of relevant code, and optionally generates a visualization.
  A probe is ONE step in an investigation — not a complete analysis.

  Examples — be specific and descriptive for best results:
    hce probe "how does authentication middleware validate tokens"
    hce probe "trace the request lifecycle from Router to Response"
    hce probe "what would break if I changed BaseModel.validate"
    hce probe "exception handling patterns in the payment module"
    hce probe "all places that validate user input before database writes"
    hce probe "how does the caching layer interact with the ORM"

  Strategies (auto-detected from your question):
    blast-radius   — impact of changing a symbol (who depends on it?)
    inheritance    — class hierarchies and overrides
    data-flow      — trace execution paths and data transformations
    exception-flow — how errors are raised, caught, and propagated
    api-surface    — public interface of a module
    cross-cutting  — patterns that appear across many files

  Output: interactive D3 visualization (.html) + analysis prompt (.md)

  Tips for constructing good queries:
    - Name specific symbols when you know them: "how does Session.commit
      interact with the connection pool" > "how does committing work"
    - Mention the module/area to focus the search: "validation in the
      forms module" > "validation"
    - Ask about relationships: "what calls X", "what inherits from Y",
      "how does X reach Y"
    - For impact analysis: "what would break if I changed X"

### Blast Radius — Single-Symbol Impact Analysis
  hce blast-radius <symbol> --depth 2 --task "description"

  Specialized version of probe for impact analysis of a specific symbol.
  Use when you know exactly which symbol you're changing.

### Investigation Workflow
  Build up evidence with multiple probe calls — tours accumulate:
    hce probe "what classes inherit from SelectorMixin"
    hce probe "how does SelectKBest.fit work"
    hce probe "what calls _get_support_mask"

  Mark weak/empty results so they don't clutter the visualization:
    hce tour annotate <tour-id> --status weak --finding "Only 2 steps, not useful"

  Reference tours by name when explaining answers to the user.
  The visualization at .hce_cache/visualization.html shows all active tours.

  Save and resume investigations:
    hce tour export --all --output my_investigation.json
    hce tour import my_investigation.json

  Start fresh:
    hce probe "new question" --clear

  Tour status values:
    active  — good results, shown in visualization (default)
    empty   — query returned nothing (important for reasoning, not shown)
    weak    — too few results to be useful visually (not shown)
    hidden  — explicitly excluded

Output is human-readable text by default. Add --json for structured
output when you need to parse the results programmatically.

The CLI returns file paths, related symbols, and grep patterns.
Prefer `hce lookup` over exploratory grepping when investigating
code relationships — it already knows the call graph and dependency
structure.

If hce returns nothing relevant, fall back to Grep/Read as normal."""

CURSOR_INSTRUCTIONS = """\
## Code Intelligence

This project is indexed by hce (Hypergraph Code Explorer).
Read `.hce/CODEBASE_MAP.md` for a structural overview.

When investigating code relationships, use the terminal:

  hce probe "your question"      # single structural probe
  hce lookup <symbol> --callers    # reverse call graph
  hce lookup <symbol> --inherits   # class hierarchy
  hce search <term>                # find symbols by name
  hce query "question"             # natural language query
  hce blast-radius <symbol>        # impact analysis

Investigation workflow — tours accumulate across multiple probe calls:
  hce probe "what inherits from X"    # first query
  hce probe "what calls Y"            # accumulates
  hce tour annotate <id> --status weak  # mark weak results
  hce tour export --all -o results.json # save for later
  hce tour import results.json          # resume

Output is human-readable by default. Add --json for structured output.
The CLI returns file paths, related symbols, and grep patterns.
Use these to navigate directly to relevant code.

If hce returns nothing relevant, fall back to standard search as normal."""

CODEX_INSTRUCTIONS = """\
## Code Intelligence

This project has a structural index at `.hce/CODEBASE_MAP.md`.
Read it for an overview of modules, key symbols, and relationships.

Use the `hce` CLI for structural queries:

  hce probe "your question"             # single structural probe
  hce lookup <symbol> --callers --depth 2  # reverse dependencies
  hce search <term>                        # find symbols by name
  hce query "natural language question"    # dispatch query
  hce blast-radius <symbol>                # impact analysis
  hce overview                             # module summary

Investigation workflow — tours accumulate across probe calls:
  hce probe "query 1"                   # first tour
  hce probe "query 2"                   # accumulates
  hce tour annotate <id> --status weak    # mark weak results
  hce tour export --all -o results.json   # save
  hce tour import results.json            # resume
  hce probe "query" --clear             # start fresh

Output is human-readable by default. Add --json for structured output.
Returns file paths, related symbols, and grep patterns."""

_TOOL_CONFIG: dict[str, tuple[str, str]] = {
    "claude-code": ("CLAUDE.md", CLAUDE_CODE_INSTRUCTIONS),
    "cursor": (".cursorrules", CURSOR_INSTRUCTIONS),
    "codex": ("AGENTS.md", CODEX_INSTRUCTIONS),
}

_SECTION_MARKER = "## Code Intelligence"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_init_file(
    tool: str,
    project_dir: Path | None = None,
) -> Path:
    """Generate a tool instruction file.

    Args:
        tool: "claude-code", "cursor", or "codex"
        project_dir: Where to write the file. Defaults to cwd.

    Returns:
        Path to the generated/updated file.
    """
    if tool not in _TOOL_CONFIG:
        raise ValueError(f"Unknown tool: {tool!r}. Choose from: {list(_TOOL_CONFIG.keys())}")

    project_dir = project_dir or Path.cwd()
    filename, instructions = _TOOL_CONFIG[tool]
    filepath = project_dir / filename

    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
        updated = _update_section(existing, instructions)
        filepath.write_text(updated, encoding="utf-8")
    else:
        filepath.write_text(instructions + "\n", encoding="utf-8")

    return filepath


def generate_all_init_files(
    project_dir: Path | None = None,
) -> list[Path]:
    """Generate instruction files for all supported tools.

    Returns:
        List of paths to generated/updated files.
    """
    paths: list[Path] = []
    for tool in _TOOL_CONFIG:
        paths.append(generate_init_file(tool, project_dir))
    return paths


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _update_section(existing_content: str, new_instructions: str) -> str:
    """Replace or append the Code Intelligence section in existing content."""
    if _SECTION_MARKER in existing_content:
        # Find the section and replace it
        lines = existing_content.split("\n")
        before: list[str] = []
        after: list[str] = []
        in_section = False
        past_section = False

        for line in lines:
            if line.strip() == _SECTION_MARKER:
                in_section = True
                continue
            elif in_section and line.startswith("## ") and line.strip() != _SECTION_MARKER:
                # Hit the next section header — end of our section
                in_section = False
                past_section = True
                after.append(line)
                continue

            if not in_section and not past_section:
                before.append(line)
            elif past_section:
                after.append(line)

        # Rebuild: before + new section + after
        result = "\n".join(before)
        if result and not result.endswith("\n"):
            result += "\n"
        result += "\n" + new_instructions + "\n"
        if after:
            result += "\n" + "\n".join(after)
        return result
    else:
        # Append section with separator
        result = existing_content
        if not result.endswith("\n"):
            result += "\n"
        result += "\n" + new_instructions + "\n"
        return result
