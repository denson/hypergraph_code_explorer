"""
Visualization
=============
Generate D3 HTML visualizations and markdown reports from a hypergraph.

Two modes:
  - **Full graph**: visualize all nodes and edges — no tours required.
    Generated automatically on index as ``.hce_cache/graph.html``.
  - **Tour-focused**: overlay reasoning tours on the graph so humans can
    follow an agent's exploration path.

The pipeline:
  1. Extract graph data (full graph or tour-focused subgraph)
  2. Optionally convert memory tours to viz overlay format
  3. Inject into the D3 HTML template and/or render a markdown report
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .graph.builder import HypergraphBuilder
from .memory_tours import MemoryTour, MemoryTourStep, MemoryTourStore

STRUCTURAL_TYPES = {
    "DEFINES", "CALLS", "INHERITS", "DECORATES",
    "RAISES", "IMPORTS", "SIGNATURE",
}

TOUR_PALETTE = [
    "#ff4466", "#00d4ff", "#ffc233", "#7cff6b",
    "#b44dff", "#ff8c42", "#00e6a1", "#ff6bcd",
    "#4d9fff", "#e6ff4d", "#ff4dbd", "#4dffe6",
]

GENERIC_SYMBOLS = {
    "True", "False", "None", "dict", "list", "body", "form", "type",
    "field", "value", "model", "data", "args", "self", "path", "line",
    "name", "str", "int", "bool", "float", "Any", "Optional",
}

_TEMPLATE_RELPATH = Path("skill") / "assets" / "viz_template.html"


# ---------------------------------------------------------------------------
# Tour selection
# ---------------------------------------------------------------------------

def select_tours(
    store: MemoryTourStore,
    *,
    tags: list[str] | None = None,
    tour_ids: list[str] | None = None,
) -> list[MemoryTour]:
    """Filter tours from the store by ID list or tags.

    If ``tour_ids`` is provided it takes precedence over ``tags``.
    If neither is provided, all tours are returned.
    Only tours with status "active" are included (unless explicitly
    selected by ID).
    """
    if tour_ids:
        # Explicit ID selection bypasses status filtering
        tours = []
        for tid in tour_ids:
            t = store.get(tid)
            if t:
                tours.append(t)
        return tours

    if tags:
        result: list[MemoryTour] = []
        seen: set[str] = set()
        for tag in tags:
            for t in store.list_tours(tag=tag):
                if t.id not in seen and t.status == "active":
                    seen.add(t.id)
                    result.append(t)
        return result

    return store.list_tours(status="active")


# ---------------------------------------------------------------------------
# Subgraph extraction
# ---------------------------------------------------------------------------

def _is_seed_related(node_id: str, seed_nodes: set[str]) -> bool:
    """Check if a node matches any seed via exact, prefix, or suffix match."""
    if node_id in seed_nodes:
        return True
    for seed in seed_nodes:
        if node_id.startswith(seed + ".") or node_id.endswith("." + seed):
            return True
        if "." in seed and node_id == seed.split(".")[-1]:
            return True
    return False


def _auto_assign_group(node_id: str, file_path: str) -> str:
    """Derive a group name from the file path or node prefix.

    Uses the parent directory of the file as the group name. If the file is
    at the package root level (parent is the package dir itself), uses the
    file stem instead.
    """
    if file_path:
        p = Path(file_path)
        parent_name = p.parent.name
        # If the parent dir looks like a meaningful subpackage, use it
        if parent_name and parent_name not in {".", ".."} and not parent_name.startswith("."):
            return parent_name
        return p.stem
    if "." in node_id:
        return node_id.split(".")[0]
    return "other"


def _group_color_from_name(group: str) -> str:
    """Deterministic hue from group name so the same module always gets the
    same color without a manual mapping table."""
    h = int(hashlib.md5(group.encode()).hexdigest()[:8], 16)
    hue = h % 360
    return f"hsl({hue}, 55%, 55%)"


def collect_seed_nodes(tours: list[MemoryTour]) -> set[str]:
    """Gather all node IDs referenced by tour steps and keywords."""
    seeds: set[str] = set()
    for tour in tours:
        for step in tour.steps:
            seeds.add(step.node)
        for kw in tour.keywords:
            seeds.add(kw)
    return seeds


def _expand_seeds(
    builder: HypergraphBuilder,
    seed_nodes: set[str],
) -> set[str]:
    """Expand seed node set with fuzzy matches from the builder's node index.

    Handles cases where a seed is ``ValidationError`` but the graph has
    ``django.forms.fields.ValidationError``.
    """
    expanded = set(seed_nodes)
    for node_id in builder._node_to_edges:
        if _is_seed_related(node_id, seed_nodes):
            expanded.add(node_id)
    return expanded


def _get_edge_type(rec) -> str:
    """Extract the edge type suffix from a HyperedgeRecord."""
    etype_raw = str(rec.edge_type)
    return etype_raw.split(".")[-1] if "." in etype_raw else etype_raw


def _compute_hop_distances(
    builder: HypergraphBuilder,
    seed_nodes: set[str],
    allowed_types: set[str],
    max_hops: int = 0,
) -> dict[str, int]:
    """BFS from all seed nodes. Returns {node_id: min_hop_distance}.

    Distance 0 = seed node. If ``max_hops`` is 0, compute for ALL reachable
    nodes (unlimited). Otherwise stop at ``max_hops``.
    """
    distances: dict[str, int] = {s: 0 for s in seed_nodes}
    frontier = set(seed_nodes)
    visited = set(seed_nodes)
    hop = 0

    while frontier:
        hop += 1
        if max_hops > 0 and hop > max_hops:
            break
        next_frontier: set[str] = set()
        for eid, rec in builder._edge_store.items():
            if _get_edge_type(rec) not in allowed_types:
                continue
            all_nodes = set(rec.sources) | set(rec.targets)
            if frontier & all_nodes:
                for n in all_nodes:
                    if n not in visited:
                        distances[n] = hop
                        visited.add(n)
                        next_frontier.add(n)
        frontier = next_frontier

    return distances


def _get_hub_nodes(builder: HypergraphBuilder, top: int = 20) -> list[str]:
    """Return the top N nodes by degree for default fog seeds."""
    return [
        node for node, _ in sorted(
            builder._node_to_edges.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )[:top]
    ]


# ---------------------------------------------------------------------------
# Pre-computed layout (delegates to layout.py)
# ---------------------------------------------------------------------------

from .layout import compute_layout


def _precompute_layout(
    nodes: list[dict],
    edges: list[dict],
) -> None:
    """Assign x,y positions to nodes in-place using numpy force simulation."""
    if not nodes:
        return

    positions = compute_layout(nodes, edges)
    for n in nodes:
        pos = positions.get(n["id"], {"x": 0.0, "y": 0.0})
        n["x"] = pos["x"]
        n["y"] = pos["y"]


# Edge type enum for compact edge encoding
EDGE_TYPE_ENUM = {
    "CALLS": 0, "IMPORTS": 1, "DEFINES": 2, "INHERITS": 3,
    "RAISES": 4, "DECORATES": 5, "SIGNATURE": 6,
}
EDGE_TYPE_NAMES = list(EDGE_TYPE_ENUM.keys())


def extract_tour_subgraph(
    builder: HypergraphBuilder,
    tours: list[MemoryTour],
    *,
    edge_types: set[str] | None = None,
    max_neighborhood_hops: int = 0,
    max_svg: int = 500,
) -> dict:
    """Extract graph data with hop-distance metadata for fog-of-war viz.

    ALL nodes reachable from tour seeds are included. Each node carries
    ``hop_distance`` and pre-computed ``x``/``y`` layout positions. The
    browser renders only a focus window of nodes as SVG; the rest appear
    as a canvas heatmap (fog).

    Args:
        edge_types: If provided, only include these edge types (overrides
            the default ``STRUCTURAL_TYPES`` filter).
        max_neighborhood_hops: Maximum hops to emit (0 = unlimited).
            Nodes beyond this are hard-pruned from the data.
        max_svg: Maximum SVG nodes in the initial focus window.

    Returns ``{"nodes": [...], "edges": [...], "group_colors": {...},
    "hub_nodes": [...], "focus_window": [...]}``
    """
    allowed_types = edge_types if edge_types is not None else STRUCTURAL_TYPES
    seed_nodes = collect_seed_nodes(tours)

    # Expand seeds via fuzzy matching
    expanded_seeds = _expand_seeds(builder, seed_nodes)

    # Compute hop distances via BFS (always unlimited for full graph data)
    distances = _compute_hop_distances(
        builder, expanded_seeds, allowed_types, 0,
    )

    # If max_neighborhood_hops > 0, also compute with cap for pruning
    if max_neighborhood_hops > 0:
        capped = _compute_hop_distances(
            builder, expanded_seeds, allowed_types, max_neighborhood_hops,
        )
        reachable_nodes = set(capped.keys())
    else:
        reachable_nodes = set(distances.keys())

    degree: dict[str, int] = defaultdict(int)
    calls_degree: dict[str, int] = defaultdict(int)
    inherits_degree: dict[str, int] = defaultdict(int)
    node_files: dict[str, str] = {}
    edges: list[dict] = []

    for eid, rec in builder._edge_store.items():
        etype = _get_edge_type(rec)
        if etype not in allowed_types:
            continue
        for s in rec.sources:
            for t in rec.targets:
                if s not in reachable_nodes or t not in reachable_nodes:
                    continue
                edges.append({
                    "source": s, "target": t,
                    "type": etype, "file": rec.source_path,
                })
                degree[s] += 1
                degree[t] += 1
                if etype == "CALLS":
                    calls_degree[s] += 1
                    calls_degree[t] += 1
                elif etype == "INHERITS":
                    inherits_degree[s] += 1
                    inherits_degree[t] += 1
                if rec.source_path:
                    node_files.setdefault(s, rec.source_path)
                    node_files.setdefault(t, rec.source_path)

    all_node_ids: set[str] = set()
    for e in edges:
        all_node_ids.add(e["source"])
        all_node_ids.add(e["target"])

    groups_seen: set[str] = set()
    nodes: list[dict] = []
    for nid in sorted(all_node_ids):
        d = degree.get(nid, 0)
        imp = 2 * (calls_degree.get(nid, 0) + inherits_degree.get(nid, 0)) + d
        if _is_seed_related(nid, seed_nodes):
            imp = max(imp, 15)
        group = _auto_assign_group(nid, node_files.get(nid, ""))
        groups_seen.add(group)
        label = nid.split(".")[-1] if "." in nid else nid
        nodes.append({
            "id": nid,
            "label": label,
            "group": group,
            "degree": d,
            "importance": imp,
            "language": "python",
            "hop_distance": distances.get(nid, -1),
        })

    # Pre-compute layout positions
    _precompute_layout(nodes, edges)

    group_colors = {g: _group_color_from_name(g) for g in sorted(groups_seen)}
    hub_nodes = _get_hub_nodes(builder, top=20)

    # Compute initial focus window: tour seeds + their 2-hop neighborhood
    focus_window = _compute_focus_window(
        nodes, expanded_seeds, max_svg,
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "group_colors": group_colors,
        "hub_nodes": hub_nodes,
        "focus_window": focus_window,
    }


def _compute_focus_window(
    nodes: list[dict],
    seed_ids: set[str],
    max_svg: int = 500,
) -> list[str]:
    """Compute the initial focus window: seed nodes + nearest neighbors by hop distance."""
    # Always include seed nodes
    seeds_in_graph = [n for n in nodes if n["id"] in seed_ids]
    others = [n for n in nodes if n["id"] not in seed_ids]

    # Sort non-seeds by hop distance (closest first), then by importance
    others.sort(key=lambda n: (n.get("hop_distance", 999) if n.get("hop_distance", -1) >= 0 else 999, -n["importance"]))

    budget = max_svg - len(seeds_in_graph)
    selected = [n["id"] for n in seeds_in_graph]
    if budget > 0:
        selected.extend(n["id"] for n in others[:budget])
    return selected


def extract_full_graph(builder: HypergraphBuilder) -> dict:
    """Extract ALL nodes and edges from the builder for full-graph viz.

    No tour filtering — includes everything. Hub nodes (top 20 by degree)
    are used as default fog seeds, so the D3 template can show them as
    the always-visible anchors with fog-of-war on everything else.

    Returns ``{"nodes": [...], "edges": [...], "group_colors": {...},
    "hub_nodes": [...]}`` in the format expected by the viz template.
    """
    degree: dict[str, int] = defaultdict(int)
    calls_degree: dict[str, int] = defaultdict(int)
    inherits_degree: dict[str, int] = defaultdict(int)
    node_files: dict[str, str] = {}
    edges: list[dict] = []

    for eid, rec in builder._edge_store.items():
        etype_raw = str(rec.edge_type)
        etype = etype_raw.split(".")[-1] if "." in etype_raw else etype_raw
        if etype not in STRUCTURAL_TYPES:
            continue
        for s in rec.sources:
            for t in rec.targets:
                edges.append({
                    "source": s,
                    "target": t,
                    "type": etype,
                    "file": rec.source_path,
                })
                degree[s] += 1
                degree[t] += 1
                if etype == "CALLS":
                    calls_degree[s] += 1
                    calls_degree[t] += 1
                elif etype == "INHERITS":
                    inherits_degree[s] += 1
                    inherits_degree[t] += 1
                if rec.source_path:
                    node_files.setdefault(s, rec.source_path)
                    node_files.setdefault(t, rec.source_path)

    all_node_ids: set[str] = set()
    for e in edges:
        all_node_ids.add(e["source"])
        all_node_ids.add(e["target"])

    # Hub nodes as default fog seeds
    hub_nodes = _get_hub_nodes(builder, top=20)
    hub_set = set(hub_nodes)

    # Compute hop distances from hub nodes for fog-of-war
    distances = _compute_hop_distances(builder, hub_set, STRUCTURAL_TYPES)

    groups_seen: set[str] = set()
    nodes: list[dict] = []
    for nid in sorted(all_node_ids):
        d = degree.get(nid, 0)
        imp = 2 * (calls_degree.get(nid, 0) + inherits_degree.get(nid, 0)) + d
        if nid in hub_set:
            imp = max(imp, 15)
        group = _auto_assign_group(nid, node_files.get(nid, ""))
        groups_seen.add(group)
        label = nid.split(".")[-1] if "." in nid else nid
        nodes.append({
            "id": nid,
            "label": label,
            "group": group,
            "degree": d,
            "importance": imp,
            "language": "python",
            "hop_distance": distances.get(nid, -1),
        })

    # Pre-compute layout positions
    _precompute_layout(nodes, edges)

    group_colors = {g: _group_color_from_name(g) for g in sorted(groups_seen)}

    # Focus window: hub nodes + nearest neighbors
    focus_window = _compute_focus_window(nodes, hub_set, max_svg=500)

    return {
        "nodes": nodes,
        "edges": edges,
        "group_colors": group_colors,
        "hub_nodes": hub_nodes,
        "focus_window": focus_window,
    }


# ---------------------------------------------------------------------------
# Memory tour → viz tour conversion
# ---------------------------------------------------------------------------

def highlight_symbols(
    text: str,
    keywords: list[str],
    graph_node_ids: set[str],
) -> str:
    """Wrap known symbol names in ``<strong class='tc'>`` tags for the D3
    sidebar narrative coloring."""
    symbols: set[str] = set()
    for kw in keywords:
        symbols.add(kw)
        if "." in kw:
            symbols.add(kw.split(".")[-1])

    # Only add graph node IDs that actually appear in the text
    # (fast substring check before expensive regex)
    for nid in graph_node_ids:
        short = nid.split(".")[-1] if "." in nid else nid
        if short in text:
            symbols.add(short)
            symbols.add(nid)

    symbols = {s for s in symbols if len(s) > 3 and s not in GENERIC_SYMBOLS}

    if not symbols:
        return text

    # Build ONE combined pattern instead of one per symbol
    sorted_symbols = sorted(symbols, key=len, reverse=True)
    combined_pattern = re.compile(
        r"(?<![<\w.])(?:" + "|".join(re.escape(s) for s in sorted_symbols) + r")(?![>\w])"
    )

    def replace_match(m: re.Match) -> str:
        return f"<strong class='tc'>{m.group()}</strong>"

    return combined_pattern.sub(replace_match, text)


def memory_tours_to_viz(
    tours: list[MemoryTour],
    graph_node_ids: set[str],
) -> list[dict]:
    """Convert ``MemoryTour`` objects to the viz template tour format."""
    # Pre-build short-name set for O(1) keyword lookup
    short_names: set[str] = set()
    for nid in graph_node_ids:
        if "." in nid:
            short_names.add(nid.split(".")[-1])
        short_names.add(nid)

    viz_tours: list[dict] = []
    for i, tour in enumerate(tours):
        color = TOUR_PALETTE[i % len(TOUR_PALETTE)]

        keywords: list[str] = []
        for step in tour.steps:
            keywords.append(step.node)
        for kw in tour.keywords:
            if kw in graph_node_ids or kw in short_names:
                keywords.append(kw)
        keywords = list(dict.fromkeys(keywords))

        steps: list[dict] = []
        for step in tour.steps:
            text = highlight_symbols(step.text, keywords, graph_node_ids)
            steps.append({"text": text, "node": step.node})

        viz_tours.append({
            "name": tour.name,
            "color": color,
            "keywords": keywords,
            "steps": steps,
        })

    return viz_tours


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _minify_data(graph_data: dict) -> dict:
    """Minify graph data for compact JSON embedding.

    - Nodes in the focus window get full fields; fog nodes get compact fields.
    - Edges are encoded as ``[srcIdx, tgtIdx, typeEnum, fileIdx]`` arrays.
    - File paths are deduplicated into a separate array.
    - All nodes include pre-computed x,y positions.
    """
    focus_set = set(graph_data.get("focus_window", []))
    raw_nodes = graph_data["nodes"]

    # Build node index for edge encoding
    node_id_to_idx: dict[str, int] = {}
    nodes: list[dict] = []
    for i, n in enumerate(raw_nodes):
        node_id_to_idx[n["id"]] = i
        in_focus = n["id"] in focus_set
        nd: dict = {
            "i": n["id"],
            "g": n["group"],
            "p": n["importance"],
        }
        if "hop_distance" in n:
            nd["h"] = n["hop_distance"]
        if "x" in n:
            nd["x"] = n["x"]
        if "y" in n:
            nd["y"] = n["y"]
        # Full fields only for focus-window nodes
        if in_focus:
            nd["l"] = n["label"]
            nd["d"] = n["degree"]
            nd["ln"] = n.get("language", "other")
        nodes.append(nd)

    # Deduplicate file paths
    file_to_idx: dict[str, int] = {}
    file_list: list[str] = []
    for e in graph_data["edges"]:
        f = e.get("file", "")
        if f and f not in file_to_idx:
            file_to_idx[f] = len(file_list)
            file_list.append(f)

    # Compact edge encoding: [srcIdx, tgtIdx, typeEnum, fileIdx]
    edges: list[list[int]] = []
    for e in graph_data["edges"]:
        si = node_id_to_idx.get(e["source"])
        ti = node_id_to_idx.get(e["target"])
        if si is None or ti is None:
            continue
        te = EDGE_TYPE_ENUM.get(e["type"], 0)
        fi = file_to_idx.get(e.get("file", ""), -1)
        edge = [si, ti, te]
        if fi >= 0:
            edge.append(fi)
        edges.append(edge)

    result: dict = {
        "n": nodes,
        "e": edges,
        "f": file_list,
        "t": EDGE_TYPE_NAMES,
    }

    hub = graph_data.get("hub_nodes", [])
    if hub:
        result["hubs"] = hub

    fw = graph_data.get("focus_window", [])
    if fw:
        result["fw"] = fw

    return result


def _find_template() -> Path:
    """Locate ``viz_template.html`` — check inside the package first, then project root."""
    pkg_dir = Path(__file__).parent
    # 1. Inside the installed package (pip install)
    bundled = pkg_dir / "assets" / "viz_template.html"
    if bundled.exists():
        return bundled
    # 2. Project root layout (git clone / editable install)
    for ancestor in [pkg_dir.parent.parent, pkg_dir.parent, pkg_dir]:
        candidate = ancestor / _TEMPLATE_RELPATH
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Could not find viz_template.html. Searched {bundled} and "
        f"relative to {pkg_dir}. Expected at <project_root>/{_TEMPLATE_RELPATH}"
    )


def _inject_template(
    graph_data: dict,
    viz_tours: list[dict],
    template: str,
    title: str,
) -> str:
    """Inject graph data, tours, and config into the HTML template."""
    raw_data = _minify_data(graph_data)
    group_colors = graph_data.get("group_colors", {})

    data_js = f"const _raw = {json.dumps(raw_data, separators=(',', ':'))};"
    tours_js = f"const tours = {json.dumps(viz_tours, separators=(',', ':'))};"
    config_js = ""
    if group_colors:
        config_js += f"const _groupColors = {json.dumps(group_colors, separators=(',', ':'))};\n"

    html = template.replace("{{TITLE}}", title)
    html = html.replace(
        "// {{DATA_INJECTION}} — replaced by generate_viz.py",
        data_js,
    )
    html = html.replace(
        "// {{TOURS_INJECTION}} — replaced by generate_viz.py",
        tours_js,
    )
    html = html.replace(
        "// {{CONFIG_INJECTION}} — replaced by generate_viz.py",
        config_js,
    )
    return html


def _generate_html_from_prepared(
    graph_data: dict,
    viz_tours: list[dict],
    output_path: Path,
    *,
    title: str = "Codebase Architecture",
    template_path: str | Path | None = None,
) -> Path:
    """Write HTML from pre-computed graph data and viz tours."""
    tpl = Path(template_path) if template_path else _find_template()
    template = tpl.read_text(encoding="utf-8")
    html = _inject_template(graph_data, viz_tours, template, title)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def generate_html(
    builder: HypergraphBuilder,
    output_path: str | Path,
    *,
    tours: list[MemoryTour] | None = None,
    edge_types: set[str] | None = None,
    max_neighborhood_hops: int = 0,
    max_svg: int = 500,
    title: str = "Codebase Architecture",
    template_path: str | Path | None = None,
) -> Path:
    """Generate a self-contained D3 HTML visualization.

    If ``tours`` are provided, extracts a focused subgraph around tour nodes
    and overlays the tours as guided walks. If ``tours`` is None or empty,
    visualizes the full graph with no tour overlays.

    Returns the path to the written HTML file.
    """
    output_path = Path(output_path)
    if tours:
        graph_data = extract_tour_subgraph(
            builder, tours,
            edge_types=edge_types,
            max_neighborhood_hops=max_neighborhood_hops,
            max_svg=max_svg,
        )
        graph_node_ids = {n["id"] for n in graph_data["nodes"]}
        viz_tours = memory_tours_to_viz(tours, graph_node_ids)
    else:
        graph_data = extract_full_graph(builder)
        viz_tours = []
    return _generate_html_from_prepared(
        graph_data, viz_tours, output_path,
        title=title, template_path=template_path,
    )


# ---------------------------------------------------------------------------
# Markdown report generation
# ---------------------------------------------------------------------------

_EDGE_TYPE_LEGEND = {
    "IMPORTS": ("`[imports]` / `[imported by]`", "Module/symbol import relationship"),
    "CALLS": ("`[calls]` / `[called by]`", "Function/method call site"),
    "DEFINES": ("`[defines]` / `[defined in]`", "Class/module defines members"),
    "INHERITS": ("`[inherits from]` / `[inherited by]`", "Class inheritance"),
    "SIGNATURE": ("`[has signature]` / `[parameter of]`", "Function parameter types"),
    "RAISES": ("`[raises]` / `[raised by]`", "Exception raise/except sites"),
    "DECORATES": ("`[decorates]` / `[decorated by]`", "Decorator application"),
}


def _classify_origin(tour: MemoryTour) -> str:
    """Heuristic: determine if a tour is auto-scaffolded, text-search, or LLM-authored."""
    if "llm-authored" in tour.tags:
        return "LLM-authored"
    has_edge_types = any(s.edge_type for s in tour.steps)
    has_text_match = any("[text match]" in s.text for s in tour.steps)
    if has_text_match:
        return "text-search"
    if has_edge_types:
        return "auto-scaffolded"
    return "LLM-authored"


def _is_narrative_step(step: MemoryTourStep) -> bool:
    """A step is narrative (LLM-authored) if it lacks bracket notation."""
    return "[" not in step.text or "[text match]" not in step.text


def generate_report(
    tours: list[MemoryTour],
    output_path: str | Path,
    *,
    title: str = "Memory Tour Report",
    target_codebase: str = "",
    total_nodes: int = 0,
    tour_nodes: int = 0,
    near_nodes: int = 0,
    far_nodes: int = 0,
) -> Path:
    """Generate a markdown report from memory tours.

    Returns the path to the written file.
    """
    output_path = Path(output_path)
    lines: list[str] = []

    promoted_count = sum(1 for t in tours if t.promoted)
    total_steps = sum(len(t.steps) for t in tours)
    unique_files: set[str] = set()
    for t in tours:
        for s in t.steps:
            if s.file:
                unique_files.add(s.file)

    # Header
    lines.append(f"# {title}")
    lines.append("")
    if target_codebase:
        lines.append(f"**Target codebase**: `{target_codebase}`")
    lines.append(f"**Tour count**: {len(tours)}")
    lines.append(f"**Promoted tours**: {promoted_count}")
    lines.append(f"**Total steps**: {total_steps}")
    if unique_files:
        lines.append(f"**Unique files touched**: ~{len(unique_files)}")
    if total_nodes:
        lines.append(
            f"**Graph fog**: Showing {total_nodes:,} nodes total. "
            f"{tour_nodes} tour nodes (always visible), "
            f"~{near_nodes:,} within 2 hops (revealed on medium zoom), "
            f"~{far_nodes:,} in fog (revealed on close zoom)."
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Tour index table
    lines.append("## Tour Index")
    lines.append("")
    lines.append("| # | Name | Steps | Tags | Promoted | Uses | Origin |")
    lines.append("|---|------|------:|------|:--------:|-----:|--------|")
    for i, t in enumerate(tours, 1):
        tags_str = ", ".join(f"`{tag}`" for tag in t.tags) if t.tags else ""
        promoted_str = "Yes" if t.promoted else ""
        origin = _classify_origin(t)
        anchor = f"#{i}-{t.name.lower().replace(' ', '-').replace(':', '')}"
        lines.append(
            f"| {i} | [{t.name}]({anchor}) | {len(t.steps)} | {tags_str} | "
            f"{promoted_str} | {t.use_count} | {origin} |"
        )
    lines.append("")

    # Edge type legend
    lines.append("### Edge type legend")
    lines.append("")
    lines.append("| Edge Type | Notation | Meaning |")
    lines.append("|-----------|----------|---------|")
    for etype, (notation, meaning) in _EDGE_TYPE_LEGEND.items():
        lines.append(f"| {etype} | {notation} | {meaning} |")
    lines.append("")

    # Per-tour sections
    for i, t in enumerate(tours, 1):
        origin = _classify_origin(t)
        lines.append("---")
        lines.append("")
        lines.append(f"## {i}. {t.name}")
        lines.append("")

        # Metadata table
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| **ID** | `{t.id}` |")
        if t.created_from_query:
            lines.append(f'| **Query** | "{t.created_from_query}" |')
        else:
            lines.append("| **Query** | *(none -- hand-crafted)* |")
        if t.tags:
            lines.append(f"| **Tags** | {', '.join(f'`{tag}`' for tag in t.tags)} |")
        lines.append(f"| **Promoted** | {'**Yes**' if t.promoted else 'No'} |")
        lines.append(f"| **Use count** | {t.use_count} |")
        lines.append(f"| **Created** | {t.created_at} |")
        lines.append("")

        # Summary
        if t.summary:
            lines.append(f"**Summary**: {t.summary}")
            lines.append("")

        # Steps
        is_narrative = origin == "LLM-authored"
        if is_narrative:
            lines.append("### Steps")
            lines.append("")
            lines.append("| # | Node | Narrative | File |")
            lines.append("|---|------|-----------|------|")
            for si, step in enumerate(t.steps, 1):
                file_str = f"`{step.file}`" if step.file else ""
                text_escaped = step.text.replace("|", "\\|")
                node_escaped = step.node.replace("|", "\\|")
                lines.append(f"| {si} | `{node_escaped}` | {text_escaped} | {file_str} |")
        else:
            max_display = 30
            display_steps = t.steps[:max_display]
            remaining = t.steps[max_display:]

            header_label = f"Steps (first {max_display} of {len(t.steps)})" if remaining else "Steps"
            lines.append(f"### {header_label}")
            lines.append("")
            lines.append("| # | Node | Relationship | File | Type |")
            lines.append("|---|------|-------------|------|------|")
            for si, step in enumerate(display_steps, 1):
                file_str = f"`{step.file}`" if step.file else ""
                etype_str = step.edge_type if step.edge_type else ""
                text_escaped = step.text.replace("|", "\\|")
                node_escaped = step.node.replace("|", "\\|")
                lines.append(
                    f"| {si} | `{node_escaped}` | {text_escaped} | {file_str} | {etype_str} |"
                )

            if remaining:
                lines.append("")
                lines.append(f"### Remaining steps ({len(remaining)} more)")
                lines.append("")
                edge_type_counts: dict[str, int] = defaultdict(int)
                frontier_count = 0
                for step in remaining:
                    if step.edge_type:
                        edge_type_counts[step.edge_type] += 1
                    else:
                        frontier_count += 1
                if edge_type_counts or frontier_count:
                    lines.append("**By edge type**:")
                    lines.append("")
                    lines.append("| Edge Type | Count |")
                    lines.append("|-----------|------:|")
                    for et, cnt in sorted(edge_type_counts.items()):
                        lines.append(f"| {et} | {cnt} |")
                    if frontier_count:
                        lines.append(f"| *(frontier/other)* | {frontier_count} |")

        lines.append("")

    # Observations
    lines.append("---")
    lines.append("")
    lines.append("## Observations")
    lines.append("")

    auto_count = sum(1 for t in tours if _classify_origin(t) == "auto-scaffolded")
    text_count = sum(1 for t in tours if _classify_origin(t) == "text-search")
    llm_count = sum(1 for t in tours if _classify_origin(t) == "LLM-authored")

    lines.append("### Tour type breakdown")
    lines.append("")
    if auto_count:
        lines.append(f"- **Auto-scaffolded**: {auto_count} tours")
    if text_count:
        lines.append(f"- **Text-search**: {text_count} tours")
    if llm_count:
        lines.append(f"- **LLM-authored**: {llm_count} tours")
    lines.append("")

    tag_counts: dict[str, int] = defaultdict(int)
    for t in tours:
        for tag in t.tags:
            tag_counts[tag] += 1
    if tag_counts:
        lines.append("### Tag distribution")
        lines.append("")
        lines.append("| Tag | Tours |")
        lines.append("|-----|------:|")
        for tag, cnt in sorted(tag_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| `{tag}` | {cnt} |")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def generate_visualization(
    builder: HypergraphBuilder,
    output_base: str | Path,
    *,
    tours: list[MemoryTour] | None = None,
    edge_types: set[str] | None = None,
    max_neighborhood_hops: int = 0,
    max_svg: int = 500,
    title: str = "Codebase Architecture",
    template_path: str | Path | None = None,
    target_codebase: str = "",
) -> dict:
    """Generate D3 HTML visualization, optionally with tour overlays.

    If ``tours`` are provided, extracts a focused subgraph and generates
    both HTML and a markdown report. If ``tours`` is None or empty,
    visualizes the full graph (HTML only, no markdown report).

    Nodes have pre-computed x/y positions and hop_distance metadata.
    The browser renders only a focus window (~max_svg nodes) as SVG;
    the rest appear as a canvas heatmap (fog).

    Args:
        builder: The loaded HypergraphBuilder.
        output_base: Base path without extension. Writes ``<base>.html``
            and optionally ``<base>.md``.
        tours: Optional memory tours to overlay as guided walks.
        edge_types: If provided, only include these edge types.
        max_neighborhood_hops: Maximum hops to emit (0 = unlimited).
        max_svg: Maximum SVG nodes in browser focus window (default: 500).
        title: Title for both outputs.
        template_path: Override path to ``viz_template.html``.
        target_codebase: Codebase name for the markdown report header.

    Returns:
        Dict with keys: ``html``, ``md`` (or None), ``tours``, ``nodes``,
        ``edges``, ``fog_tour_nodes``, ``fog_near``, ``fog_far``.
    """
    output_base = Path(output_base)
    html_path = output_base.with_suffix(".html")

    if tours:
        graph_data = extract_tour_subgraph(
            builder, tours,
            edge_types=edge_types,
            max_neighborhood_hops=max_neighborhood_hops,
            max_svg=max_svg,
        )
        graph_node_ids = {n["id"] for n in graph_data["nodes"]}
        viz_tours = memory_tours_to_viz(tours, graph_node_ids)
    else:
        graph_data = extract_full_graph(builder)
        viz_tours = []

    html_out = _generate_html_from_prepared(
        graph_data, viz_tours, html_path,
        title=title, template_path=template_path,
    )

    # Compute fog stats from hop distances
    all_nodes = graph_data["nodes"]
    tour_nodes = sum(1 for n in all_nodes if n.get("hop_distance", -1) == 0)
    near_nodes = sum(1 for n in all_nodes if 0 < n.get("hop_distance", -1) <= 2)
    far_nodes = sum(1 for n in all_nodes if n.get("hop_distance", -1) > 2 or n.get("hop_distance", -1) == -1)

    md_out = None
    if tours:
        md_path = output_base.with_suffix(".md")
        report_title = f"Memory Tour Report: {title}" if title != "Codebase Architecture" else "Memory Tour Report"
        md_out = str(generate_report(
            tours, md_path,
            title=report_title,
            target_codebase=target_codebase,
            total_nodes=len(all_nodes),
            tour_nodes=tour_nodes,
            near_nodes=near_nodes,
            far_nodes=far_nodes,
        ))

    return {
        "html": str(html_out),
        "md": md_out,
        "tours": len(tours) if tours else 0,
        "nodes": len(all_nodes),
        "edges": len(graph_data["edges"]),
        "fog_tour_nodes": tour_nodes,
        "fog_near": near_nodes,
        "fog_far": far_nodes,
    }
