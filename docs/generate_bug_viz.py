#!/usr/bin/env python3
"""
Generate a self-contained D3 visualization for bug #13399 memory tours.

Reads:
  - target_repos/fastapi/fastapi/.hce_cache/builder.pkl  (graph data)
  - docs/bug_memory_tours.json                            (memory tours)
  - skill/assets/viz_template.html                        (D3 template)

Writes:
  - docs/bug_13399_viz.html                               (self-contained output)
"""

import json
import pickle
import re
import sys
import types
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Flexible EdgeType mock (from extract_graph.py)
# ---------------------------------------------------------------------------

class FlexEdgeType:
    def __init__(self, value):
        self.value = value
    def __repr__(self):
        return f"EdgeType.{self.value}"
    def __str__(self):
        return self.value
    def __eq__(self, other):
        if isinstance(other, FlexEdgeType):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return False
    def __hash__(self):
        return hash(self.value)


class EdgeTypeMeta(type):
    _cache = {}
    def __call__(cls, value):
        if value not in cls._cache:
            cls._cache[value] = FlexEdgeType(value)
        return cls._cache[value]
    def __getattr__(cls, name):
        return FlexEdgeType(name)


class EdgeType(metaclass=EdgeTypeMeta):
    pass


for mod_name in [
    "hypergraph_code_explorer",
    "hypergraph_code_explorer.models",
    "hypergraph_code_explorer.graph",
    "hypergraph_code_explorer.graph.builder",
]:
    sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules["hypergraph_code_explorer.models"].EdgeType = EdgeType


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
CACHE_DIR = PROJECT_ROOT / "target_repos" / "fastapi" / "fastapi" / ".hce_cache"
TOURS_PATH = PROJECT_ROOT / "docs" / "bug_memory_tours.json"
TEMPLATE_PATH = PROJECT_ROOT / "skill" / "assets" / "viz_template.html"
OUTPUT_PATH = PROJECT_ROOT / "docs" / "bug_13399_viz.html"

STRUCTURAL_TYPES = {"DEFINES", "CALLS", "INHERITS", "DECORATES", "RAISES", "IMPORTS", "SIGNATURE"}

FILE_TO_GROUP = {
    "dependencies/utils.py": "utils",
    "dependencies/models.py": "models",
    "routing.py": "routing",
    "params.py": "params",
    "_compat/v2.py": "compat",
    "_compat/shared.py": "compat",
    "_compat/__init__.py": "compat",
    "applications.py": "applications",
    "exceptions.py": "exceptions",
    "security/": "security",
}

GROUP_COLORS = {
    "utils": "#ff6b6b",
    "routing": "#4ecdc4",
    "params": "#ffe66d",
    "compat": "#a8e6cf",
    "models": "#c3aed6",
    "applications": "#7ec8e3",
    "exceptions": "#ff9a76",
    "security": "#b8d4e3",
    "other": "#555570",
}

TOUR_COLORS = ["#ff4466", "#00d4ff"]


# ---------------------------------------------------------------------------
# Step 1: Extract focused subgraph from builder.pkl
# ---------------------------------------------------------------------------

def load_builder(cache_dir: Path):
    pkl_path = cache_dir / "builder.pkl"
    with open(pkl_path, "rb") as f:
        return pickle.load(f)


def get_edge_store(data):
    if isinstance(data, dict):
        return data.get("edge_store", {})
    if hasattr(data, "_edge_store"):
        return data._edge_store
    raise ValueError(f"Unexpected pickle format: {type(data)}")


def collect_seed_nodes(tours_data: dict) -> set[str]:
    """Gather all node IDs referenced by tour steps and keywords."""
    seeds = set()
    for tour in tours_data["tours"]:
        for step in tour["steps"]:
            seeds.add(step["node"])
        for kw in tour["keywords"]:
            seeds.add(kw)
    return seeds


def assign_group(node_id: str, file_path: str) -> str:
    for pattern, group in FILE_TO_GROUP.items():
        if pattern in file_path:
            return group
    parts = node_id.split(".")
    if len(parts) > 1:
        return parts[0]
    return "other"


def extract_subgraph(edge_store: dict, seed_nodes: set[str]) -> dict:
    """Extract edges involving seed nodes + 1-hop neighbors, compute node metadata."""
    degree = defaultdict(int)
    calls_degree = defaultdict(int)
    inherits_degree = defaultdict(int)
    node_files: dict[str, str] = {}
    edges = []

    # Prefix matching: a seed "utils._get_multidict_value" should match
    # node IDs like "utils._get_multidict_value" exactly, and a seed like
    # "Form" should match "Form", "params.Form", etc.
    def is_seed_related(node_id: str) -> bool:
        if node_id in seed_nodes:
            return True
        for seed in seed_nodes:
            if node_id.startswith(seed + ".") or node_id.endswith("." + seed):
                return True
            if "." in seed and node_id == seed.split(".")[-1]:
                return True
        return False

    # First pass: find all edges touching seed nodes
    relevant_edge_ids = set()
    for eid, rec in edge_store.items():
        if isinstance(rec, dict):
            src_list = rec.get("sources", [])
            tgt_list = rec.get("targets", [])
        else:
            src_list = getattr(rec, "sources", [])
            tgt_list = getattr(rec, "targets", [])

        all_nodes = set(src_list) | set(tgt_list)
        if any(is_seed_related(n) for n in all_nodes):
            relevant_edge_ids.add(eid)

    # Second pass: extract edges and compute degrees
    neighbor_nodes = set()
    for eid in relevant_edge_ids:
        rec = edge_store[eid]
        if isinstance(rec, dict):
            etype = str(rec.get("edge_type", ""))
            src_list = rec.get("sources", [])
            tgt_list = rec.get("targets", [])
            source_path = rec.get("source_path", "")
        else:
            etype = str(getattr(rec, "edge_type", ""))
            src_list = getattr(rec, "sources", [])
            tgt_list = getattr(rec, "targets", [])
            source_path = getattr(rec, "source_path", "")

        if etype not in STRUCTURAL_TYPES:
            continue

        for s in src_list:
            for t in tgt_list:
                edges.append({
                    "source": s,
                    "target": t,
                    "type": etype,
                    "file": source_path,
                })
                degree[s] += 1
                degree[t] += 1
                neighbor_nodes.add(s)
                neighbor_nodes.add(t)
                if etype == "CALLS":
                    calls_degree[s] += 1
                    calls_degree[t] += 1
                elif etype == "INHERITS":
                    inherits_degree[s] += 1
                    inherits_degree[t] += 1
                if source_path:
                    node_files.setdefault(s, source_path)
                    node_files.setdefault(t, source_path)

    all_node_ids = set()
    for e in edges:
        all_node_ids.add(e["source"])
        all_node_ids.add(e["target"])

    nodes = []
    for nid in sorted(all_node_ids):
        d = degree.get(nid, 0)
        imp = 2 * (calls_degree.get(nid, 0) + inherits_degree.get(nid, 0)) + d
        # Boost importance for seed nodes so they're visually prominent
        if is_seed_related(nid):
            imp = max(imp, 15)
        group = assign_group(nid, node_files.get(nid, ""))
        label = nid.split(".")[-1] if "." in nid else nid
        nodes.append({
            "id": nid,
            "label": label,
            "group": group,
            "degree": d,
            "importance": imp,
            "language": "python",
        })

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Step 2: Convert memory tours to viz tour format
# ---------------------------------------------------------------------------

def convert_tours(tours_data: dict, graph_node_ids: set[str]) -> list[dict]:
    """Convert memory tour format to the viz template's tour format."""
    viz_tours = []
    for i, tour in enumerate(tours_data["tours"]):
        color = TOUR_COLORS[i % len(TOUR_COLORS)]

        # Collect keywords: tour step nodes + original keywords that match graph nodes
        keywords = []
        for step in tour["steps"]:
            keywords.append(step["node"])
        for kw in tour["keywords"]:
            if kw in graph_node_ids or any(nid.startswith(kw) or nid.endswith("." + kw) for nid in graph_node_ids):
                keywords.append(kw)
        keywords = list(dict.fromkeys(keywords))  # dedupe preserving order

        steps = []
        for step in tour["steps"]:
            text = step["text"]
            # Wrap node references in <strong class='tc'> for coloring
            text = highlight_symbols(text, keywords, graph_node_ids)
            steps.append({
                "text": text,
                "node": step["node"],
            })

        viz_tours.append({
            "name": tour["name"],
            "color": color,
            "keywords": keywords,
            "steps": steps,
        })

    return viz_tours


def highlight_symbols(text: str, keywords: list[str], graph_node_ids: set[str]) -> str:
    """Wrap known symbol names in <strong class='tc'> tags."""
    # Collect all symbol names worth highlighting
    symbols = set()
    for kw in keywords:
        symbols.add(kw)
        if "." in kw:
            symbols.add(kw.split(".")[-1])
    for nid in graph_node_ids:
        if "." in nid:
            symbols.add(nid.split(".")[-1])
        symbols.add(nid)

    # Filter to reasonably specific names (skip very short/generic ones)
    symbols = {s for s in symbols if len(s) > 3 and s not in {
        "True", "False", "None", "dict", "list", "body", "form", "type",
        "field", "value", "model", "data", "args", "self", "path", "line",
    }}

    # Sort longest-first to avoid partial matches
    sorted_symbols = sorted(symbols, key=len, reverse=True)

    # Track which ranges are already wrapped to avoid double-wrapping
    already_wrapped = []

    for sym in sorted_symbols:
        pattern = re.compile(r'(?<![<\w.])' + re.escape(sym) + r'(?![>\w])')
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if any(s <= start < e or s < end <= e for s, e in already_wrapped):
                continue
            replacement = f"<strong class='tc'>{sym}</strong>"
            text = text[:start] + replacement + text[end:]
            offset = len(replacement) - (end - start)
            already_wrapped = [(s + offset if s > start else s, e + offset if e > start else e) for s, e in already_wrapped]
            already_wrapped.append((start, start + len(replacement)))
            break  # re-scan after each replacement

    return text


# ---------------------------------------------------------------------------
# Step 3: Generate HTML
# ---------------------------------------------------------------------------

def minify_data(graph_data: dict) -> dict:
    nodes = [{
        "i": n["id"],
        "l": n["label"],
        "g": n["group"],
        "d": n["degree"],
        "p": n["importance"],
        "ln": n.get("language", "other"),
    } for n in graph_data["nodes"]]
    edges = [{
        "s": e["source"],
        "t": e["target"],
        "y": e["type"],
        "f": e.get("file", ""),
    } for e in graph_data["edges"]]
    return {"n": nodes, "e": edges}


def generate_html(graph_data: dict, viz_tours: list[dict], template: str) -> str:
    title = "FastAPI Bug #13399: Form Default Prefill"
    raw_data = minify_data(graph_data)

    data_js = f"const _raw = {json.dumps(raw_data, separators=(',', ':'))};"
    tours_js = f"const tours = {json.dumps(viz_tours, separators=(',', ':'))};"
    config_js = f"const _groupColors = {json.dumps(GROUP_COLORS, separators=(',', ':'))};\n"

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading builder.pkl...")
    builder_data = load_builder(CACHE_DIR)
    edge_store = get_edge_store(builder_data)
    print(f"  {len(edge_store)} edges in full graph")

    print("Loading memory tours...")
    with open(TOURS_PATH) as f:
        tours_data = json.load(f)
    print(f"  {len(tours_data['tours'])} tours")

    print("Collecting seed nodes...")
    seeds = collect_seed_nodes(tours_data)
    print(f"  {len(seeds)} seed nodes")

    print("Extracting subgraph...")
    graph_data = extract_subgraph(edge_store, seeds)
    print(f"  {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges")

    print("Converting tours to viz format...")
    graph_node_ids = {n["id"] for n in graph_data["nodes"]}
    viz_tours = convert_tours(tours_data, graph_node_ids)
    for t in viz_tours:
        print(f"  Tour: {t['name']} ({len(t['steps'])} steps, {len(t['keywords'])} keywords)")

    print("Loading template...")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    print("Generating HTML...")
    html = generate_html(graph_data, viz_tours, template)

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\nWrote {OUTPUT_PATH}")
    print(f"  {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges, {len(viz_tours)} tours")
    print(f"  Size: {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
