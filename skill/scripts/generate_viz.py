#!/usr/bin/env python3
"""
Generate a self-contained HCE visualization HTML from graph data and tours.

Usage:
    python generate_viz.py <graph.json> <tours.json> <output.html> [--title "My Project"]

Inputs:
  - graph.json: Output from extract_graph.py
    {nodes: [{id, label, group, degree, importance, language}, ...],
     edges: [{source, target, type, file}, ...]}

  - tours.json: Tour definitions designed by Claude Code
    {
      "title": "My Project Architecture",
      "group_colors": {"routing": "#00d4ff", "models": "#ff8c42", ...},
      "group_colors_cb": {"routing": "#56B4E9", "models": "#E69F00", ...},
      "tours": [
        {
          "name": "Tour Name",
          "color": "#00d4ff",
          "keywords": ["prefix1", "prefix2"],
          "steps": [
            {"text": "HTML narrative with <strong class='tc'>symbol</strong>", "node": "symbol.id"},
            ...
          ]
        },
        ...
      ]
    }

Output:
  - A single self-contained HTML file with all data embedded.
"""

import json
import sys
from pathlib import Path


def minify_data(graph_data):
    """Convert full graph JSON to compact format with short keys."""
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


def generate_viz(graph_path, tours_path, output_path, title=None):
    """Read template, inject data and tours, write final HTML."""
    # Find the template relative to this script
    script_dir = Path(__file__).parent.resolve()
    template_path = script_dir.parent / "assets" / "viz_template.html"

    if not template_path.exists():
        print(f"Error: template not found at {template_path}", file=sys.stderr)
        sys.exit(1)

    template = template_path.read_text(encoding="utf-8")

    # Load inputs
    with open(graph_path) as f:
        graph_data = json.load(f)

    with open(tours_path) as f:
        tours_data = json.load(f)

    # Use title from tours.json, CLI arg, or fallback
    viz_title = title or tours_data.get("title", "Codebase Architecture")

    # Minify graph data
    raw_data = minify_data(graph_data)

    # Build JS injection strings
    data_js = f"const _raw = {json.dumps(raw_data, separators=(',', ':'))};"

    tours_js = f"const tours = {json.dumps(tours_data.get('tours', []), separators=(',', ':'))};"

    # Group colors — provide defaults Claude Code can override
    gc = tours_data.get("group_colors", {})
    gc_cb = tours_data.get("group_colors_cb", {})
    config_js = ""
    if gc:
        config_js += f"const _groupColors = {json.dumps(gc, separators=(',', ':'))};\n"
    if gc_cb:
        config_js += f"const _groupColorsCB = {json.dumps(gc_cb, separators=(',', ':'))};\n"

    # Replace placeholders in template
    html = template.replace("{{TITLE}}", viz_title)

    # Replace the injection comment lines with actual JS
    html = html.replace(
        "// {{DATA_INJECTION}} — replaced by generate_viz.py",
        data_js
    )
    html = html.replace(
        "// {{TOURS_INJECTION}} — replaced by generate_viz.py",
        tours_js
    )
    html = html.replace(
        "// {{CONFIG_INJECTION}} — replaced by generate_viz.py",
        config_js
    )

    # Also replace the two {{TITLE}} in the HTML body
    # (already handled by the first replace since it's the same marker)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    node_count = len(graph_data["nodes"])
    edge_count = len(graph_data["edges"])
    tour_count = len(tours_data.get("tours", []))
    langs = set(n.get("language", "other") for n in graph_data["nodes"])

    print(f"Generated {output_path}")
    print(f"  {node_count} nodes, {edge_count} edges, {tour_count} tours")
    print(f"  Languages: {', '.join(sorted(langs))}")
    print(f"  Size: {Path(output_path).stat().st_size / 1024:.0f} KB")


def main():
    if len(sys.argv) < 4:
        print("Usage: python generate_viz.py <graph.json> <tours.json> <output.html> [--title \"Title\"]")
        sys.exit(1)

    graph_path = sys.argv[1]
    tours_path = sys.argv[2]
    output_path = sys.argv[3]

    title = None
    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        if idx + 1 < len(sys.argv):
            title = sys.argv[idx + 1]

    generate_viz(graph_path, tours_path, output_path, title)


if __name__ == "__main__":
    main()
