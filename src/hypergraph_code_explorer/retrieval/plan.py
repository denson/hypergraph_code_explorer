"""
RetrievalPlan Data Model
========================
Structured output from the tiered retrieval system. A RetrievalPlan tells the
consuming agent which files to read, which symbols to grep for, what
relationships exist, and why each matters.

Formatters produce human-readable text (default), JSON, or YAML output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FileSuggestion:
    """A file the agent should read, with reasons and specific symbols."""
    path: str
    symbols: list[str] = field(default_factory=list)
    reason: str = ""
    priority: int = 1  # 1 = most important

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "symbols": self.symbols,
            "reason": self.reason,
            "priority": self.priority,
        }


@dataclass
class GrepSuggestion:
    """A grep pattern the agent should run to find relevant code."""
    pattern: str
    scope: str = ""  # file or directory glob
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "scope": self.scope,
            "reason": self.reason,
        }


@dataclass
class SymbolRelation:
    """A named relationship between symbols found in the graph."""
    name: str
    file: str = ""
    relationship: str = ""  # "calls", "inherits from", "imported by", etc.
    edge_type: str = ""     # EdgeType value
    targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "file": self.file,
            "relationship": self.relationship,
            "edge_type": self.edge_type,
        }
        if self.targets:
            d["targets"] = self.targets
        return d


@dataclass
class Overview:
    """High-level codebase summary for Tier 5 responses."""
    modules: list[dict] = field(default_factory=list)
    key_symbols: list[dict] = field(default_factory=list)
    reading_order: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "modules": self.modules,
            "key_symbols": self.key_symbols,
            "reading_order": self.reading_order,
        }


@dataclass
class RetrievalPlan:
    """Complete retrieval result from the tiered dispatch system."""
    query: str
    classification: list[str] = field(default_factory=list)
    tiers_used: list[int] = field(default_factory=list)

    primary_files: list[FileSuggestion] = field(default_factory=list)
    grep_suggestions: list[GrepSuggestion] = field(default_factory=list)
    related_symbols: list[SymbolRelation] = field(default_factory=list)
    structural_context: str = ""
    overview: Overview | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "query": self.query,
            "classification": self.classification,
            "tiers_used": self.tiers_used,
            "primary_files": [f.to_dict() for f in self.primary_files],
            "grep_suggestions": [g.to_dict() for g in self.grep_suggestions],
            "related_symbols": [s.to_dict() for s in self.related_symbols],
            "structural_context": self.structural_context,
        }
        if self.overview:
            d["overview"] = self.overview.to_dict()
        return d

    def is_empty(self) -> bool:
        """True if the plan has no actionable content."""
        return (
            not self.primary_files
            and not self.grep_suggestions
            and not self.related_symbols
            and not self.structural_context
        )

    def merge(self, other: RetrievalPlan) -> None:
        """Merge another plan's results into this one (for multi-tier dispatch)."""
        for t in other.tiers_used:
            if t not in self.tiers_used:
                self.tiers_used.append(t)
        for c in other.classification:
            if c not in self.classification:
                self.classification.append(c)

        # Merge files, dedup by path
        existing_paths = {f.path for f in self.primary_files}
        for f in other.primary_files:
            if f.path not in existing_paths:
                self.primary_files.append(f)
                existing_paths.add(f.path)

        # Merge greps, dedup by pattern+scope
        existing_greps = {(g.pattern, g.scope) for g in self.grep_suggestions}
        for g in other.grep_suggestions:
            key = (g.pattern, g.scope)
            if key not in existing_greps:
                self.grep_suggestions.append(g)
                existing_greps.add(key)

        # Merge symbols, dedup by name+relationship
        existing_rels = {(s.name, s.relationship) for s in self.related_symbols}
        for s in other.related_symbols:
            key = (s.name, s.relationship)
            if key not in existing_rels:
                self.related_symbols.append(s)
                existing_rels.add(key)

        # Append structural context
        if other.structural_context:
            if self.structural_context:
                self.structural_context += "\n" + other.structural_context
            else:
                self.structural_context = other.structural_context

        # Take overview if we don't have one
        if other.overview and not self.overview:
            self.overview = other.overview


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _relative_path(path: str) -> str:
    """Try to make a path relative for display."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd()))
    except (ValueError, OSError):
        return path


def format_text(plan: RetrievalPlan) -> str:
    """Format a RetrievalPlan as human-readable text."""
    lines: list[str] = []

    # Header
    lines.append(f"Query: {plan.query}")
    lines.append(f"Tiers used: {plan.tiers_used}")
    lines.append("")

    # Files
    if plan.primary_files:
        lines.append("=== Files to Read ===")
        for f in sorted(plan.primary_files, key=lambda x: x.priority):
            path = _relative_path(f.path)
            syms = ", ".join(f.symbols) if f.symbols else ""
            reason = f" -- {f.reason}" if f.reason else ""
            if syms:
                lines.append(f"  [{f.priority}] {path}  ({syms}){reason}")
            else:
                lines.append(f"  [{f.priority}] {path}{reason}")
        lines.append("")

    # Grep suggestions (capped to keep output actionable)
    MAX_DISPLAY_GREPS = 15
    if plan.grep_suggestions:
        lines.append("=== Grep Suggestions ===")
        for g in plan.grep_suggestions[:MAX_DISPLAY_GREPS]:
            scope = f" in {g.scope}" if g.scope else ""
            reason = f"  -- {g.reason}" if g.reason else ""
            lines.append(f"  grep -rn '{g.pattern}'{scope}{reason}")
        if len(plan.grep_suggestions) > MAX_DISPLAY_GREPS:
            lines.append(f"  ... (+{len(plan.grep_suggestions) - MAX_DISPLAY_GREPS} more, use --json for full list)")
        lines.append("")

    # Related symbols
    if plan.related_symbols:
        lines.append("=== Related Symbols ===")
        for s in plan.related_symbols:
            file_str = f" ({_relative_path(s.file)})" if s.file else ""
            targets_str = ""
            if s.targets:
                targets_str = " -> " + ", ".join(s.targets)
            lines.append(f"  {s.name}{file_str} [{s.relationship}]{targets_str}")
        lines.append("")

    # Structural context
    if plan.structural_context:
        lines.append("=== Context ===")
        lines.append(plan.structural_context)
        lines.append("")

    if plan.is_empty():
        lines.append("No results found.")

    return "\n".join(lines)


def format_json(plan: RetrievalPlan) -> str:
    """Format a RetrievalPlan as JSON."""
    return json.dumps(plan.to_dict(), indent=2)
