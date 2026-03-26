"""
Memory Tours
============
Agent-facing persistent tours that capture useful architectural paths and
subgraphs for reuse across sessions. Stored as a sidecar JSON file alongside
the graph cache, independent of visualization tours.

A MemoryTour is structurally similar to a visualization tour (name, steps
anchored to node IDs) but carries provenance metadata — the query that created
it, confidence, tags, timestamps — so an LLM can decide which past tours are
relevant to a new task.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


SIDECAR_FILENAME = "memory_tours.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MemoryTourStep:
    """A single step in a memory tour, anchored to a graph node."""
    node: str
    text: str
    file: str = ""
    edge_type: str = ""
    context_query: str = ""

    def to_dict(self) -> dict:
        d: dict = {"node": self.node, "text": self.text}
        if self.file:
            d["file"] = self.file
        if self.edge_type:
            d["edge_type"] = self.edge_type
        if self.context_query:
            d["context_query"] = self.context_query
        return d

    @classmethod
    def from_dict(cls, d: dict) -> MemoryTourStep:
        return cls(
            node=d["node"],
            text=d["text"],
            file=d.get("file", ""),
            edge_type=d.get("edge_type", ""),
            context_query=d.get("context_query", ""),
        )


@dataclass
class MemoryTour:
    """A persisted agent-oriented tour over the code graph.

    Fields mirror the visualization tour shape (name, keywords, steps) but add
    provenance and reuse metadata that visualization tours don't carry.
    """
    id: str
    name: str
    summary: str
    keywords: list[str] = field(default_factory=list)
    steps: list[MemoryTourStep] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # Provenance
    created_from_query: str = ""
    created_at: str = ""
    promoted: bool = False

    # Status and metadata
    status: str = "active"          # "active" | "empty" | "weak" | "hidden"
    strategy: str = ""              # Primary strategy used (blast-radius, etc.)
    finding: str = ""               # Agent's interpretation of results
    parent_tour_id: str = ""        # If follow-up, which tour prompted it
    step_count: int = 0             # len(steps) at creation time

    # Reuse tracking
    last_used_at: str = ""
    use_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def touch(self) -> None:
        """Record a usage of this tour."""
        self.last_used_at = datetime.now(timezone.utc).isoformat()
        self.use_count += 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "keywords": self.keywords,
            "steps": [s.to_dict() for s in self.steps],
            "tags": self.tags,
            "created_from_query": self.created_from_query,
            "created_at": self.created_at,
            "promoted": self.promoted,
            "status": self.status,
            "strategy": self.strategy,
            "finding": self.finding,
            "parent_tour_id": self.parent_tour_id,
            "step_count": self.step_count,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MemoryTour:
        return cls(
            id=d.get("id", ""),
            name=d["name"],
            summary=d.get("summary", ""),
            keywords=d.get("keywords", []),
            steps=[MemoryTourStep.from_dict(s) for s in d.get("steps", [])],
            tags=d.get("tags", []),
            created_from_query=d.get("created_from_query", ""),
            created_at=d.get("created_at", ""),
            promoted=d.get("promoted", False),
            status=d.get("status", "active"),
            strategy=d.get("strategy", ""),
            finding=d.get("finding", ""),
            parent_tour_id=d.get("parent_tour_id", ""),
            step_count=d.get("step_count", 0),
            last_used_at=d.get("last_used_at", ""),
            use_count=d.get("use_count", 0),
        )


# ---------------------------------------------------------------------------
# Sidecar persistence
# ---------------------------------------------------------------------------

class MemoryTourStore:
    """File-backed store for memory tours, persisted as a JSON sidecar next to
    the graph cache.

    The sidecar path is ``<cache_dir>/memory_tours.json``. The store is
    append-friendly: callers load, mutate, then save. No locking is performed —
    the assumption is single-writer (CLI or MCP process) per cache directory.
    """

    def __init__(self, cache_dir: str | Path):
        self._cache_dir = Path(cache_dir)
        self._path = self._cache_dir / SIDECAR_FILENAME
        self._tours: dict[str, MemoryTour] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        for td in raw.get("tours", []):
            tour = MemoryTour.from_dict(td)
            self._tours[tour.id] = tour

    def save(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "tours": [t.to_dict() for t in self._tours.values()],
        }
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---- CRUD ----------------------------------------------------------

    def add(self, tour: MemoryTour) -> MemoryTour:
        """Add a tour and persist immediately."""
        self._tours[tour.id] = tour
        self.save()
        return tour

    def get(self, tour_id: str) -> MemoryTour | None:
        return self._tours.get(tour_id)

    def list_tours(
        self,
        *,
        tag: str | None = None,
        promoted_only: bool = False,
        status: str | None = None,
        exclude_status: list[str] | None = None,
    ) -> list[MemoryTour]:
        tours = list(self._tours.values())
        if tag:
            tours = [t for t in tours if tag in t.tags]
        if promoted_only:
            tours = [t for t in tours if t.promoted]
        if status:
            tours = [t for t in tours if t.status == status]
        if exclude_status:
            exclude = set(exclude_status)
            tours = [t for t in tours if t.status not in exclude]
        return sorted(tours, key=lambda t: t.created_at, reverse=True)

    def set_status(self, tour_id: str, status: str) -> MemoryTour | None:
        """Update a tour's status. Valid: active, empty, weak, hidden."""
        valid = {"active", "empty", "weak", "hidden"}
        if status not in valid:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {valid}")
        tour = self._tours.get(tour_id)
        if tour:
            tour.status = status
            self.save()
        return tour

    def clear(self) -> None:
        """Remove all tours."""
        self._tours.clear()
        self.save()

    def remove(self, tour_id: str) -> bool:
        if tour_id in self._tours:
            del self._tours[tour_id]
            self.save()
            return True
        return False

    def promote(self, tour_id: str) -> MemoryTour | None:
        """Mark an ephemeral tour as promoted (persistent memory)."""
        tour = self._tours.get(tour_id)
        if tour:
            tour.promoted = True
            self.save()
        return tour

    def touch(self, tour_id: str) -> MemoryTour | None:
        """Record usage of a tour."""
        tour = self._tours.get(tour_id)
        if tour:
            tour.touch()
            self.save()
        return tour

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._tours)


# ---------------------------------------------------------------------------
# Scaffold: derive a MemoryTour from a RetrievalPlan
# ---------------------------------------------------------------------------

def scaffold_from_plan(
    plan,  # RetrievalPlan — imported dynamically to avoid circular deps
    *,
    name: str = "",
    tags: list[str] | None = None,
) -> MemoryTour:
    """Build a candidate MemoryTour from a RetrievalPlan result.

    This extracts the key symbols, files, and structural context from the plan
    and organises them into tour steps. The resulting tour is ephemeral until
    explicitly promoted.
    """
    steps: list[MemoryTourStep] = []
    keywords: list[str] = []
    seen_nodes: set[str] = set()

    for sym in plan.related_symbols:
        if sym.name in seen_nodes:
            continue
        seen_nodes.add(sym.name)
        keywords.append(sym.name)

        text = sym.name
        if sym.relationship:
            text += f" [{sym.relationship}]"
        if sym.targets:
            text += " -> " + ", ".join(sym.targets)

        steps.append(MemoryTourStep(
            node=sym.name,
            text=text,
            file=sym.file,
            edge_type=sym.edge_type,
        ))

    # Also pull in primary files as context steps when they mention symbols
    # not already covered by the relation steps
    for fs in plan.primary_files:
        for sym_name in fs.symbols:
            if sym_name not in seen_nodes:
                seen_nodes.add(sym_name)
                keywords.append(sym_name)
                reason = fs.reason or f"in {fs.path}"
                steps.append(MemoryTourStep(
                    node=sym_name,
                    text=f"{sym_name} — {reason}",
                    file=fs.path,
                ))

    tour_name = name or f"Tour: {plan.query}"
    summary_parts = [f"Query: {plan.query}"]
    if plan.classification:
        summary_parts.append(f"Classification: {', '.join(plan.classification)}")
    summary_parts.append(f"{len(steps)} steps, {len(plan.primary_files)} files")

    return MemoryTour(
        id="",  # auto-generated
        name=tour_name,
        summary="; ".join(summary_parts),
        keywords=keywords,
        steps=steps,
        tags=tags or [],
        created_from_query=plan.query,
        promoted=False,
    )


# ---------------------------------------------------------------------------
# Scaffold prompt: produce a structured context block for an LLM to author
# a richer memory tour from a RetrievalPlan
# ---------------------------------------------------------------------------

def scaffold_prompt(
    plan,  # RetrievalPlan
    *,
    existing_tour_names: list[str] | None = None,
) -> str:
    """Generate a prompt payload that an LLM can use to author a memory tour.

    The prompt includes the plan's symbols, files, and structural context, and
    asks for a JSON memory tour in the expected schema.
    """
    lines: list[str] = []
    lines.append(f"Query: {plan.query}")
    lines.append("")

    if plan.primary_files:
        lines.append("Files involved:")
        for f in plan.primary_files[:15]:
            syms = ", ".join(f.symbols) if f.symbols else ""
            lines.append(f"  - {f.path}" + (f" ({syms})" if syms else ""))
        lines.append("")

    if plan.related_symbols:
        lines.append("Symbols and relationships:")
        for s in plan.related_symbols[:30]:
            targets_str = " -> " + ", ".join(s.targets) if s.targets else ""
            lines.append(f"  - {s.name} [{s.relationship}]{targets_str}")
        lines.append("")

    if plan.structural_context:
        lines.append("Structural context:")
        lines.append(plan.structural_context[:2000])
        lines.append("")

    existing = ", ".join(existing_tour_names) if existing_tour_names else "(none)"
    lines.append(f"Existing memory tours: {existing}")
    lines.append("")
    lines.append(
        "Based on the above graph query results, create a memory tour that "
        "captures the key architectural insight. Output JSON in this format:"
    )
    lines.append("""
{
  "name": "Short descriptive title",
  "summary": "1-2 sentence summary of the architectural insight",
  "keywords": ["symbol1", "symbol2"],
  "tags": ["optional-tag"],
  "steps": [
    {"node": "symbol.id", "text": "Why this symbol matters and how it connects"},
    ...
  ]
}""")
    lines.append("")
    lines.append(
        "Focus on architectural patterns and dependencies. Each step should "
        "explain why the symbol matters and what would break without it."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Analysis prompt: convert a tour into a Stage 2 analysis work order
# ---------------------------------------------------------------------------

PROMPT_PREAMBLES: dict[str, str] = {
    "blast-radius": (
        "You are performing a blast-radius / impact analysis. Walk through the "
        "following tour step by step. At each step, read the indicated file, "
        "assess how this code depends on or is affected by the target symbol, "
        "and build on your findings from previous steps."
    ),
    "inheritance": (
        "You are mapping a class inheritance hierarchy. Walk through the "
        "following tour step by step. At each step, read the indicated file, "
        "identify what the class inherits, what it overrides, and how its "
        "behavior specializes or extends its parent classes."
    ),
    "data-flow": (
        "You are tracing data flow through the codebase. Walk through the "
        "following tour step by step. At each step, read the indicated file, "
        "identify what data enters this function, how it is transformed, and "
        "where it flows next."
    ),
    "exception-flow": (
        "You are analyzing exception and error handling patterns. Walk through "
        "the following tour step by step. At each step, read the indicated file, "
        "identify what exceptions are raised, caught, or propagated, and how "
        "error conditions are handled."
    ),
    "api-surface": (
        "You are mapping the public API surface. Walk through the following "
        "tour step by step. At each step, read the indicated file, identify "
        "what is publicly exported, what contracts it exposes, and how it "
        "relates to the overall module interface."
    ),
    "cross-cutting": (
        "You are finding all usages of a pattern across the codebase. Walk "
        "through the following tour step by step. At each step, read the "
        "indicated file, identify how this location uses the pattern, and "
        "whether the usage is consistent with other locations."
    ),
    "exploration": (
        "You are exploring the codebase to answer a question. Walk through "
        "the following tour step by step. At each step, read the indicated "
        "file, gather evidence relevant to the question, and build on your "
        "findings from previous steps."
    ),
}

PROMPT_OUTPUT_FORMATS: dict[str, str] = {
    "blast-radius": (
        "## Output format\n"
        "For each step, provide:\n"
        "1. What the code does at this location (with file:line references)\n"
        "2. Answer to the context query\n"
        "3. Risk assessment (high/medium/low) with justification\n"
        "4. Any connections to findings from previous steps\n\n"
        "After all steps, provide a summary of the complete blast radius — "
        "what would break, what might break, and what is safe."
    ),
    "inheritance": (
        "## Output format\n"
        "For each step, provide:\n"
        "1. What this class inherits from and what it overrides (with file:line references)\n"
        "2. How its behavior specializes or extends the parent\n"
        "3. Any mixins or multiple inheritance patterns\n"
        "4. Connections to findings from previous steps\n\n"
        "After all steps, provide a summary of the complete inheritance tree — "
        "the root classes, key branches, override patterns, and any diamond inheritance issues."
    ),
    "data-flow": (
        "## Output format\n"
        "For each step, provide:\n"
        "1. What data enters and exits this function (with file:line references)\n"
        "2. What transformations or validations are applied\n"
        "3. Where the data flows next\n"
        "4. Connections to findings from previous steps\n\n"
        "After all steps, provide a summary of the complete data flow path — "
        "entry points, transformations, validation checkpoints, and final destinations."
    ),
    "exception-flow": (
        "## Output format\n"
        "For each step, provide:\n"
        "1. What exceptions are raised, caught, or propagated (with file:line references)\n"
        "2. What error conditions trigger them\n"
        "3. How errors are recovered from or surfaced to the caller\n"
        "4. Connections to findings from previous steps\n\n"
        "After all steps, provide a summary of the exception handling architecture — "
        "which layers raise, which catch, which propagate, and any gaps in error handling."
    ),
    "api-surface": (
        "## Output format\n"
        "For each step, provide:\n"
        "1. What is publicly exported (with file:line references)\n"
        "2. What contract or interface it exposes\n"
        "3. Whether it is documented and stable\n"
        "4. Connections to findings from previous steps\n\n"
        "After all steps, provide a summary of the public API surface — "
        "key entry points, stability guarantees, and any undocumented public symbols."
    ),
    "cross-cutting": (
        "## Output format\n"
        "For each step, provide:\n"
        "1. How this location uses the pattern (with file:line references)\n"
        "2. Whether the usage is consistent with other locations\n"
        "3. Any deviations or edge cases\n"
        "4. Connections to findings from previous steps\n\n"
        "After all steps, provide a summary of the pattern usage — "
        "how many locations, how consistent, and any outliers or violations."
    ),
    "exploration": (
        "## Output format\n"
        "For each step, provide:\n"
        "1. What the code does at this location (with file:line references)\n"
        "2. How it relates to the question\n"
        "3. Key insights or surprising findings\n"
        "4. Connections to findings from previous steps\n\n"
        "After all steps, provide a comprehensive answer to the original question, "
        "citing the evidence gathered from each step."
    ),
}


def generate_analysis_prompt(
    tour: MemoryTour,
    *,
    task_description: str = "",
    output_format: str = "markdown",
    strategy: str = "",
) -> str:
    """Convert a memory tour into a structured analysis prompt.

    This is the Stage 2 entry point for tour-guided analysis. The returned
    prompt instructs an LLM to walk through the tour step by step, answering
    each step's context_query with evidence from the code.
    """
    if not strategy:
        known = set(PROMPT_PREAMBLES.keys())
        for tag in tour.tags:
            if tag in known:
                strategy = tag
                break
        if not strategy:
            strategy = "exploration"

    lines: list[str] = []
    lines.append(PROMPT_PREAMBLES.get(strategy, PROMPT_PREAMBLES["exploration"]))
    lines.append("")

    if task_description:
        lines.append(f"Task: {task_description}")
        lines.append("")

    lines.append(f"## Tour: {tour.name}")
    lines.append("")

    for i, step in enumerate(tour.steps, 1):
        lines.append(f"### Step {i}: {step.node}")
        if step.file:
            lines.append(f"- File: {step.file}")
        if step.edge_type:
            lines.append(f"- Relationship: {step.edge_type}")
        if step.context_query:
            lines.append(f"- Question: {step.context_query}")
        lines.append(f"- Context: {step.text}")
        lines.append("")
        lines.append("[Answer this question with evidence before moving to the next step.]")
        lines.append("")

    lines.append(PROMPT_OUTPUT_FORMATS.get(strategy, PROMPT_OUTPUT_FORMATS["exploration"]))

    return "\n".join(lines)