"""
Module-Level Summaries
======================
Generate 2-3 sentence summaries per file using Anthropic API.
Stored as SUMMARY edge type with low type_weight (0.3).
"""

from __future__ import annotations

import time
from collections import defaultdict
from hashlib import md5

from ..graph.builder import HypergraphBuilder
from ..models import EdgeType, HyperedgeRecord


_SUMMARY_PROMPT = """Given these structural relationships extracted from {source_path}:

{edge_list}

Write a 2-3 sentence summary of what this file does, what its key responsibilities are,
and what other parts of the codebase it connects to. Be specific — name the important
classes, functions, and modules. Do not be generic.

Also list the 3-5 most important entity names mentioned (as a comma-separated list on a separate line starting with "KEY ENTITIES:").
"""


def generate_summaries(
    builder: HypergraphBuilder,
    anthropic_client,
    model: str = "claude-haiku-4-5-20251001",
    paths: list[str] | None = None,
    force: bool = False,
    verbose: bool = False,
) -> list[dict]:
    """
    Generate file-level summaries and store as SUMMARY edges.

    Args:
        builder: The hypergraph builder
        anthropic_client: Anthropic client instance
        model: Model to use for summary generation
        paths: Specific file paths to summarize (None = all)
        force: Regenerate even if summaries exist
        verbose: Print progress

    Returns:
        List of summary info dicts
    """
    # Group edges by source file
    edges_by_file: dict[str, list[HyperedgeRecord]] = defaultdict(list)
    for record in builder._edge_store.values():
        if record.edge_type != EdgeType.SUMMARY:
            edges_by_file[record.source_path].append(record)

    if paths:
        edges_by_file = {k: v for k, v in edges_by_file.items() if k in paths}

    results: list[dict] = []
    skipped = 0

    for source_path, edges in sorted(edges_by_file.items()):
        summary_eid = f"summary__{source_path}"

        # Skip if already exists and not forcing
        if not force and summary_eid in builder._edge_store:
            skipped += 1
            continue

        if verbose:
            print(f"  Summarizing: {source_path} ({len(edges)} edges)")

        # Format edge list
        edge_list = "\n".join(
            f"- [{e.edge_type}] {e.relation}" for e in edges[:30]
        )

        prompt = _SUMMARY_PROMPT.format(
            source_path=source_path,
            edge_list=edge_list,
        )

        try:
            response = anthropic_client.messages.create(
                model=model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text
        except Exception as e:
            if verbose:
                print(f"    Error: {e}")
            continue

        # Parse summary and key entities
        summary_text, key_entities = _parse_summary_response(raw_text)

        # Remove old summary if forcing
        if force and summary_eid in builder._edge_store:
            builder.remove_edge(summary_eid)

        # Create SUMMARY edge
        record = HyperedgeRecord(
            edge_id=summary_eid,
            relation="summarises",
            edge_type=EdgeType.SUMMARY,
            sources=[source_path],
            targets=[],
            all_nodes=set(key_entities),
            source_path=source_path,
            chunk_id=f"summary__{source_path}",
            chunk_text=summary_text,
            metadata={
                "summary_level": "file",
                "edge_count": len(edges),
            },
        )
        builder.add_edge(record)

        results.append({
            "edge_id": summary_eid,
            "source_path": source_path,
            "summary": summary_text,
            "key_entities": key_entities,
            "edge_count": len(edges),
        })

    return results


def _parse_summary_response(text: str) -> tuple[str, list[str]]:
    """Parse the summary text and extract key entities."""
    lines = text.strip().split("\n")
    summary_lines: list[str] = []
    key_entities: list[str] = []

    for line in lines:
        if line.strip().upper().startswith("KEY ENTITIES:"):
            entities_str = line.split(":", 1)[1].strip()
            key_entities = [e.strip() for e in entities_str.split(",") if e.strip()]
        else:
            summary_lines.append(line)

    summary_text = "\n".join(summary_lines).strip()

    # If no KEY ENTITIES line found, extract from summary
    if not key_entities:
        words = summary_text.split()
        # Simple heuristic: CamelCase or dotted names
        for w in words:
            clean = w.strip(".,;:()")
            if (any(c.isupper() for c in clean[1:]) or "." in clean) and len(clean) > 2:
                key_entities.append(clean)
                if len(key_entities) >= 5:
                    break

    return summary_text, key_entities[:5]
