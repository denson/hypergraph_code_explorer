"""
CODEBASE_MAP.md Generator
==========================
Generates a static structural overview of the codebase from the hypergraph.
This file is included in agent context automatically (CLAUDE.md, .cursorrules, etc.)
and gives the agent enough structural knowledge to know when to call ``hce``.

Content caps (for large codebases):
  - Top 100 symbols by degree
  - Top 20 call chains by depth
  - Top 10 inheritance trees
  - No line numbers anywhere
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .graph.builder import HypergraphBuilder


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_module_descriptions(builder: HypergraphBuilder) -> dict[str, str]:
    """Derive one-line descriptions per source_path.

    Uses SUMMARY edge relation text if available, otherwise DEFINES edges.
    """
    descriptions: dict[str, str] = {}

    # Try SUMMARY edges first
    for rec in builder._edge_store.values():
        if rec.edge_type == "SUMMARY" and rec.source_path:
            descriptions[rec.source_path] = rec.relation

    # Fill in from DEFINES for files without a summary
    defines_by_file: dict[str, list[str]] = defaultdict(list)
    for rec in builder._edge_store.values():
        if rec.edge_type == "DEFINES" and rec.source_path:
            for t in rec.targets:
                defines_by_file[rec.source_path].append(t.rsplit(".", 1)[-1])

    for path, symbols in defines_by_file.items():
        if path not in descriptions:
            syms = ", ".join(symbols[:5])
            if len(symbols) > 5:
                syms += f", ... (+{len(symbols) - 5} more)"
            descriptions[path] = f"defines {syms}"

    return descriptions


def _get_key_symbols(
    builder: HypergraphBuilder,
    max_symbols: int,
) -> list[tuple[str, str, int]]:
    """Return (symbol, file, degree) sorted by degree descending."""
    # Build symbol -> file mapping from DEFINES edges
    symbol_file: dict[str, str] = {}
    for rec in builder._edge_store.values():
        if rec.edge_type == "DEFINES":
            for t in rec.targets:
                if t not in symbol_file:
                    symbol_file[t] = rec.source_path
            for s in rec.sources:
                if s not in symbol_file:
                    symbol_file[s] = rec.source_path

    # Fallback: any edge
    for rec in builder._edge_store.values():
        for n in rec.all_nodes:
            if n not in symbol_file:
                symbol_file[n] = rec.source_path

    nodes_by_degree: list[tuple[str, int]] = []
    for node, edge_ids in builder._node_to_edges.items():
        nodes_by_degree.append((node, len(edge_ids)))

    nodes_by_degree.sort(key=lambda x: x[1], reverse=True)

    result: list[tuple[str, str, int]] = []
    for node, degree in nodes_by_degree[:max_symbols]:
        file_path = symbol_file.get(node, "")
        # Use just the filename for readability
        if file_path:
            file_path = Path(file_path).name
        result.append((node, file_path, degree))
    return result


def _get_call_chains(
    builder: HypergraphBuilder,
    max_chains: int,
) -> list[list[str]]:
    """Walk CALLS edges to build call chains, longest first."""
    # Build adjacency: source -> set of targets via CALLS
    call_targets: dict[str, list[str]] = defaultdict(list)
    for rec in builder._edge_store.values():
        if rec.edge_type == "CALLS":
            for s in rec.sources:
                for t in rec.targets:
                    if t not in call_targets[s]:
                        call_targets[s].append(t)

    # Sort starting nodes by out-degree
    start_nodes = sorted(call_targets.keys(),
                         key=lambda n: len(call_targets[n]), reverse=True)

    chains: list[list[str]] = []
    seen_starts: set[str] = set()

    for start in start_nodes:
        if start in seen_starts:
            continue
        chain = [start]
        current = start
        visited: set[str] = {start}
        while current in call_targets:
            targets = call_targets[current]
            next_node = None
            for t in targets:
                if t not in visited:
                    next_node = t
                    break
            if next_node is None:
                break
            chain.append(next_node)
            visited.add(next_node)
            current = next_node

        if len(chain) >= 2:
            chains.append(chain)
            seen_starts.add(start)

    # Remove chains that are subsets of longer chains
    chains.sort(key=len, reverse=True)
    filtered: list[list[str]] = []
    for chain in chains:
        chain_set = set(chain)
        is_subset = False
        for longer in filtered:
            if chain_set <= set(longer):
                is_subset = True
                break
        if not is_subset:
            filtered.append(chain)

    return filtered[:max_chains]


def _get_inheritance_trees(
    builder: HypergraphBuilder,
    max_trees: int,
) -> list[tuple[str, list[str]]]:
    """Collect INHERITS edges grouped by base class."""
    children: dict[str, list[str]] = defaultdict(list)
    for rec in builder._edge_store.values():
        if rec.edge_type == "INHERITS":
            for base in rec.targets:
                for child in rec.sources:
                    if child not in children[base]:
                        children[base].append(child)

    trees = [(base, sorted(kids)) for base, kids in children.items()]
    trees.sort(key=lambda x: len(x[1]), reverse=True)
    return trees[:max_trees]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_codemap(
    builder: HypergraphBuilder,
    cache_dir: Path | str | None = None,
    max_symbols: int = 100,
    max_call_chains: int = 20,
    max_inheritance: int = 10,
) -> str:
    """Generate CODEBASE_MAP.md content and optionally save to disk.

    Args:
        builder: The populated hypergraph builder.
        cache_dir: If provided, save to cache_dir/CODEBASE_MAP.md.
        max_symbols: Cap for key symbols table.
        max_call_chains: Cap for call chains.
        max_inheritance: Cap for inheritance trees.

    Returns:
        The generated markdown string.
    """
    lines: list[str] = []

    lines.append("# Code Map")
    lines.append("<!-- Auto-generated by hce. Regenerate with: hce index <path> -->")
    lines.append("")

    # --- Modules ---
    descriptions = _get_module_descriptions(builder)
    all_paths: set[str] = set()
    for rec in builder._edge_store.values():
        if rec.source_path:
            all_paths.add(rec.source_path)

    if all_paths:
        lines.append("## Modules")
        for path in sorted(all_paths):
            desc = descriptions.get(path, "")
            fname = Path(path).name
            if desc:
                lines.append(f"- {fname} \u2014 {desc}")
            else:
                lines.append(f"- {fname}")
        lines.append("")

    # --- Key Symbols ---
    key_symbols = _get_key_symbols(builder, max_symbols)
    if key_symbols:
        lines.append(f"## Key Symbols (top {len(key_symbols)} by connectivity)")
        lines.append("| Symbol | File | Degree |")
        lines.append("|--------|------|--------|")
        for sym, file, degree in key_symbols:
            lines.append(f"| {sym} | {file} | {degree} |")
        lines.append("")

    # --- Call Chains ---
    call_chains = _get_call_chains(builder, max_call_chains)
    if call_chains:
        lines.append(f"## Call Chains (top {len(call_chains)} by depth)")
        for chain in call_chains:
            lines.append("- " + " \u2192 ".join(chain))
        lines.append("")

    # --- Inheritance Trees ---
    trees = _get_inheritance_trees(builder, max_inheritance)
    if trees:
        lines.append(f"## Inheritance Trees (top {len(trees)})")
        for base, children in trees:
            lines.append(f"- {base} \u2190 {', '.join(children)}")
        lines.append("")

    # --- CLI Quick Reference ---
    lines.append("## CLI Quick Reference")
    lines.append("```")
    lines.append("  hce lookup <symbol> --calls    # what does this symbol call?")
    lines.append("  hce lookup <symbol> --inherits # class hierarchy")
    lines.append("  hce search \"term\"              # find symbols by name")
    lines.append("  hce query \"question\"           # natural language query")
    lines.append("  # Add --json to any command for structured output")
    lines.append("```")
    lines.append("")

    content = "\n".join(lines)

    if cache_dir:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = cache_dir / "CODEBASE_MAP.md"
        out_path.write_text(content, encoding="utf-8")

    return content
