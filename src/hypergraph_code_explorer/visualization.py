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
    """
    if tour_ids:
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
                if t.id not in seen:
                    seen.add(t.id)
                    result.append(t)
        return result

    return store.list_tours()


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
    max_hops: int = 2,
) -> dict[str, int]:
    """BFS from seed nodes, returns {node_id: min_hop_distance}.

    Nodes at distance 0 are seeds, 1-max_hops are near neighborhood,
    anything beyond is not in the returned dict.
    """
    distances: dict[str, int] = {s: 0 for s in seed_nodes}
    frontier = set(seed_nodes)

    for hop in range(1, max_hops + 1):
        next_frontier: set[str] = set()
        for eid, rec in builder._edge_store.items():
            if _get_edge_type(rec) not in allowed_types:
                continue
            all_nodes = set(rec.sources) | set(rec.targets)
            if frontier & all_nodes:
                for n in all_nodes:
                    if n not in distances:
                        distances[n] = hop
                        next_frontier.add(n)
        frontier = next_frontier

    return distances


def _build_cluster_nodes(
    builder: HypergraphBuilder,
    near_nodes: set[str],
    allowed_types: set[str],
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Build cluster placeholder nodes for far-away nodes.

    Returns:
        cluster_nodes: list of node dicts for D3 (with cluster fields)
        cluster_edges: list of edge dicts connecting clusters to near nodes
        cluster_members: dict mapping cluster_id -> {"nodes": [...], "edges": [...]}
    """
    # Single pass: find far nodes and their connections to near nodes
    far_nodes: dict[str, str] = {}  # node_id -> file_path
    far_to_near: dict[str, set[str]] = defaultdict(set)  # far_node -> connected near nodes

    for eid, rec in builder._edge_store.items():
        if _get_edge_type(rec) not in allowed_types:
            continue
        all_nodes = set(rec.sources) | set(rec.targets)
        near_in_edge = all_nodes & near_nodes
        far_in_edge = all_nodes - near_nodes
        if near_in_edge and far_in_edge:
            for n in far_in_edge:
                far_nodes[n] = rec.source_path or "unknown"
                far_to_near[n] |= near_in_edge

    # Group by module (file path)
    module_groups: dict[str, list[str]] = defaultdict(list)
    for node_id, file_path in far_nodes.items():
        parts = file_path.replace("\\", "/").split("/")
        module_key = "/".join(parts[-3:]) if len(parts) >= 3 else file_path
        module_groups[module_key].append(node_id)

    cluster_nodes: list[dict] = []
    cluster_edges: list[dict] = []
    cluster_members: dict[str, dict] = {}

    for module_key, members in module_groups.items():
        cluster_id = f"__cluster__{hashlib.md5(module_key.encode()).hexdigest()[:12]}"

        # Build member node dicts for expansion
        member_node_dicts: list[dict] = []
        for mid in members:
            label = mid.split(".")[-1] if "." in mid else mid
            member_node_dicts.append({
                "id": mid,
                "label": label,
                "group": _auto_assign_group(mid, far_nodes.get(mid, "")),
                "degree": 0,
                "importance": 1,
                "language": "python",
            })

        # Build boundary edges (member <-> near node) for expansion
        member_edge_dicts: list[dict] = []
        member_set = set(members)
        for eid, rec in builder._edge_store.items():
            etype = _get_edge_type(rec)
            if etype not in allowed_types:
                continue
            all_in_edge = set(rec.sources) | set(rec.targets)
            if (all_in_edge & member_set) and (all_in_edge & near_nodes):
                for s in rec.sources:
                    for t in rec.targets:
                        if (s in member_set or t in member_set):
                            member_edge_dicts.append({
                                "source": s, "target": t,
                                "type": etype, "file": rec.source_path or "",
                            })

        cluster_members[cluster_id] = {
            "nodes": member_node_dicts,
            "edges": member_edge_dicts,
        }

        module_label = module_key.split("/")[-1] if "/" in module_key else module_key
        cluster_nodes.append({
            "id": cluster_id,
            "label": f"+{len(members)} in {module_label}",
            "full_label": f"+{len(members)} nodes in {module_key}",
            "group": module_key.split("/")[-1] if "/" in module_key else module_key,
            "importance": min(len(members), 25),
            "degree": 0,
            "is_cluster": True,
            "member_count": len(members),
            "module_path": module_key,
            "language": "python",
            "file": "",
        })

        # Add edges from cluster to boundary near-nodes
        connected_near: set[str] = set()
        for mid in members:
            connected_near |= far_to_near.get(mid, set())
        for near_id in connected_near:
            cluster_edges.append({
                "source": near_id,
                "target": cluster_id,
                "type": "CLUSTER",
                "file": module_key,
            })

    return cluster_nodes, cluster_edges, cluster_members


def extract_tour_subgraph(
    builder: HypergraphBuilder,
    tours: list[MemoryTour],
    *,
    edge_types: set[str] | None = None,
    max_neighborhood_hops: int = 2,
) -> dict:
    """Extract a pruned subgraph around tour seed nodes with cluster collapse.

    Nodes within ``max_neighborhood_hops`` of any tour node are included as
    real nodes. Nodes beyond that are collapsed into cluster placeholders
    grouped by file/module.

    Args:
        edge_types: If provided, only include these edge types (overrides
            the default ``STRUCTURAL_TYPES`` filter).
        max_neighborhood_hops: How many hops from seed nodes to include
            as visible (default: 2). Set to -1 to disable pruning.

    Returns ``{"nodes": [...], "edges": [...], "group_colors": {...},
    "cluster_members": {...}}`` in the format expected by the viz template.
    """
    allowed_types = edge_types if edge_types is not None else STRUCTURAL_TYPES
    seed_nodes = collect_seed_nodes(tours)

    # Expand seeds via fuzzy matching
    expanded_seeds = _expand_seeds(builder, seed_nodes)

    # Compute hop distances via BFS
    if max_neighborhood_hops >= 0:
        distances = _compute_hop_distances(
            builder, expanded_seeds, allowed_types, max_neighborhood_hops,
        )
        near_nodes = set(distances.keys())
    else:
        # Pruning disabled — include all connected nodes (old behavior)
        near_nodes = None  # sentinel: include everything

    degree: dict[str, int] = defaultdict(int)
    calls_degree: dict[str, int] = defaultdict(int)
    inherits_degree: dict[str, int] = defaultdict(int)
    node_files: dict[str, str] = {}
    edges: list[dict] = []

    for eid, rec in builder._edge_store.items():
        etype = _get_edge_type(rec)
        if etype not in allowed_types:
            continue
        all_nodes = set(rec.sources) | set(rec.targets)
        # Include edge if at least one node is near (or pruning disabled)
        if near_nodes is not None and not (all_nodes & near_nodes):
            continue
        # Only include edges where BOTH endpoints are near
        for s in rec.sources:
            for t in rec.targets:
                if near_nodes is not None and (s not in near_nodes or t not in near_nodes):
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
        hop = distances.get(nid, -1) if max_neighborhood_hops >= 0 else 0
        nodes.append({
            "id": nid,
            "label": label,
            "group": group,
            "degree": d,
            "importance": imp,
            "language": "python",
            "hop_distance": hop,
            "is_cluster": False,
        })

    # Build cluster placeholders for far nodes
    cluster_members: dict[str, dict] = {}
    if near_nodes is not None:
        cluster_nodes, cluster_edges, cluster_members = _build_cluster_nodes(
            builder, near_nodes, allowed_types,
        )
        nodes.extend(cluster_nodes)
        edges.extend(cluster_edges)
        for cn in cluster_nodes:
            groups_seen.add(cn["group"])

    group_colors = {g: _group_color_from_name(g) for g in sorted(groups_seen)}

    return {
        "nodes": nodes,
        "edges": edges,
        "group_colors": group_colors,
        "cluster_members": cluster_members,
    }


def extract_full_graph(builder: HypergraphBuilder) -> dict:
    """Extract ALL nodes and edges from the builder for full-graph viz.

    No tour filtering — includes everything. The D3 template handles
    visual triage via importance-based sizing/opacity.

    Returns ``{"nodes": [...], "edges": [...], "group_colors": {...}}``
    in the format expected by the viz template injection pipeline.
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

    groups_seen: set[str] = set()
    nodes: list[dict] = []
    for nid in sorted(all_node_ids):
        d = degree.get(nid, 0)
        imp = 2 * (calls_degree.get(nid, 0) + inherits_degree.get(nid, 0)) + d
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
        })

    group_colors = {g: _group_color_from_name(g) for g in sorted(groups_seen)}

    return {"nodes": nodes, "edges": edges, "group_colors": group_colors}


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
    for nid in graph_node_ids:
        if "." in nid:
            symbols.add(nid.split(".")[-1])
        symbols.add(nid)

    symbols = {s for s in symbols if len(s) > 3 and s not in GENERIC_SYMBOLS}

    sorted_symbols = sorted(symbols, key=len, reverse=True)
    already_wrapped: list[tuple[int, int]] = []

    for sym in sorted_symbols:
        pattern = re.compile(r"(?<![<\w.])" + re.escape(sym) + r"(?![>\w])")
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if any(s <= start < e or s < end <= e for s, e in already_wrapped):
                continue
            replacement = f"<strong class='tc'>{sym}</strong>"
            text = text[:start] + replacement + text[end:]
            offset = len(replacement) - (end - start)
            already_wrapped = [
                (s + offset if s > start else s, e + offset if e > start else e)
                for s, e in already_wrapped
            ]
            already_wrapped.append((start, start + len(replacement)))
            break  # re-scan after replacement

    return text


def memory_tours_to_viz(
    tours: list[MemoryTour],
    graph_node_ids: set[str],
) -> list[dict]:
    """Convert ``MemoryTour`` objects to the viz template tour format."""
    viz_tours: list[dict] = []
    for i, tour in enumerate(tours):
        color = TOUR_PALETTE[i % len(TOUR_PALETTE)]

        keywords: list[str] = []
        for step in tour.steps:
            keywords.append(step.node)
        for kw in tour.keywords:
            if kw in graph_node_ids or any(
                nid.startswith(kw) or nid.endswith("." + kw)
                for nid in graph_node_ids
            ):
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
    nodes = []
    for n in graph_data["nodes"]:
        nd: dict = {
            "i": n["id"],
            "l": n["label"],
            "g": n["group"],
            "d": n["degree"],
            "p": n["importance"],
            "ln": n.get("language", "other"),
        }
        if n.get("is_cluster"):
            nd["c"] = True
            nd["mc"] = n["member_count"]
            nd["fl"] = n.get("full_label", "")
            nd["mp"] = n.get("module_path", "")
        nodes.append(nd)
    edges = [{
        "s": e["source"],
        "t": e["target"],
        "y": e["type"],
        "f": e.get("file", ""),
    } for e in graph_data["edges"]]

    result: dict = {"n": nodes, "e": edges}

    # Minify cluster member data for click-to-expand
    cm = graph_data.get("cluster_members", {})
    if cm:
        minified_cm: dict = {}
        for cid, data in cm.items():
            minified_cm[cid] = {
                "n": [{
                    "i": mn["id"], "l": mn["label"], "g": mn["group"],
                    "d": mn["degree"], "p": mn["importance"],
                    "ln": mn.get("language", "other"),
                } for mn in data["nodes"]],
                "e": [{
                    "s": me["source"], "t": me["target"],
                    "y": me["type"], "f": me.get("file", ""),
                } for me in data["edges"]],
            }
        result["cm"] = minified_cm

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
    max_neighborhood_hops: int = 2,
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
    cluster_count: int = 0,
    clustered_node_count: int = 0,
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
    if cluster_count:
        lines.append(
            f"**Graph pruning**: Collapsed {clustered_node_count:,} nodes "
            f"into {cluster_count} module clusters"
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
    max_neighborhood_hops: int = 2,
    title: str = "Codebase Architecture",
    template_path: str | Path | None = None,
    target_codebase: str = "",
) -> dict:
    """Generate D3 HTML visualization, optionally with tour overlays.

    If ``tours`` are provided, extracts a focused subgraph and generates
    both HTML and a markdown report. If ``tours`` is None or empty,
    visualizes the full graph (HTML only, no markdown report).

    Args:
        builder: The loaded HypergraphBuilder.
        output_base: Base path without extension. Writes ``<base>.html``
            and optionally ``<base>.md``.
        tours: Optional memory tours to overlay as guided walks.
        edge_types: If provided, only include these edge types in the
            subgraph (overrides the default structural types filter).
        max_neighborhood_hops: How many hops from tour nodes to include
            as visible (default: 2). Nodes beyond are cluster-collapsed.
        title: Title for both outputs.
        template_path: Override path to ``viz_template.html``.
        target_codebase: Codebase name for the markdown report header.

    Returns:
        Dict with keys: ``html``, ``md`` (or None), ``tours``, ``nodes``,
        ``edges``, ``clusters``.
    """
    output_base = Path(output_base)
    html_path = output_base.with_suffix(".html")

    if tours:
        graph_data = extract_tour_subgraph(
            builder, tours,
            edge_types=edge_types,
            max_neighborhood_hops=max_neighborhood_hops,
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

    cluster_members = graph_data.get("cluster_members", {})
    cluster_count = len(cluster_members)
    clustered_node_count = sum(
        len(v["nodes"]) for v in cluster_members.values()
    )

    md_out = None
    if tours:
        md_path = output_base.with_suffix(".md")
        report_title = f"Memory Tour Report: {title}" if title != "Codebase Architecture" else "Memory Tour Report"
        md_out = str(generate_report(
            tours, md_path,
            title=report_title,
            target_codebase=target_codebase,
            cluster_count=cluster_count,
            clustered_node_count=clustered_node_count,
        ))

    real_nodes = sum(1 for n in graph_data["nodes"] if not n.get("is_cluster"))
    return {
        "html": str(html_out),
        "md": md_out,
        "tours": len(tours) if tours else 0,
        "nodes": real_nodes,
        "edges": len(graph_data["edges"]),
        "clusters": cluster_count,
        "clustered_nodes": clustered_node_count,
    }
