"""Tests for blast radius analysis: context_query, blast_radius(), generate_analysis_prompt()."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypergraph_code_explorer.graph.builder import HypergraphBuilder
from hypergraph_code_explorer.memory_tours import (
    MemoryTour,
    MemoryTourStep,
    generate_analysis_prompt,
)
from hypergraph_code_explorer.models import HyperedgeRecord
from hypergraph_code_explorer.visualization import extract_tour_subgraph


def _make_record(edge_id, sources, targets, edge_type="CALLS", source_path="test.py"):
    return HyperedgeRecord(
        edge_id=edge_id, relation=f"{edge_id} relation",
        edge_type=edge_type, sources=sources, targets=targets,
        source_path=source_path, chunk_id=f"chunk_{edge_id}",
    )


def _build_test_graph():
    """Build a small graph with multiple edge types for testing."""
    builder = HypergraphBuilder()
    builder.add_edge(_make_record("e1", ["validate"], ["ValidationError"], "CALLS", "validators.py"))
    builder.add_edge(_make_record("e2", ["clean"], ["validate"], "CALLS", "forms.py"))
    builder.add_edge(_make_record("e3", ["to_python"], ["ValidationError"], "RAISES", "fields.py"))
    builder.add_edge(_make_record("e4", ["CoercionError"], ["ValidationError"], "INHERITS", "exceptions.py"))
    builder.add_edge(_make_record("e5", ["forms"], ["validators"], "IMPORTS", "forms.py"))
    builder.add_edge(_make_record("e6", ["Widget"], ["validate"], "CALLS", "widgets.py"))
    builder.add_edge(_make_record("e7", ["validate"], ["int", "str"], "SIGNATURE", "validators.py"))
    return builder


# ---------------------------------------------------------------------------
# Test 3: context_query round-trips through to_dict / from_dict
# ---------------------------------------------------------------------------

def test_context_query_round_trip():
    step = MemoryTourStep(
        node="validate",
        text="validates input",
        file="validators.py",
        edge_type="CALLS",
        context_query="Would this break if ValidationError changes?",
    )
    d = step.to_dict()
    assert d["context_query"] == "Would this break if ValidationError changes?"

    restored = MemoryTourStep.from_dict(d)
    assert restored.context_query == step.context_query
    assert restored.node == step.node


def test_context_query_omitted_when_empty():
    step = MemoryTourStep(node="x", text="y")
    d = step.to_dict()
    assert "context_query" not in d

    restored = MemoryTourStep.from_dict(d)
    assert restored.context_query == ""


# ---------------------------------------------------------------------------
# Test 1 & 4: blast_radius() returns a tour with context_query fields
# ---------------------------------------------------------------------------

def test_blast_radius_returns_tour_with_context_queries():
    builder = _build_test_graph()

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        builder.save(cache_dir / "builder.pkl")

        from hypergraph_code_explorer.api import HypergraphSession
        session = HypergraphSession(builder, cache_dir=cache_dir)

        tour = session.blast_radius("ValidationError", depth=1)

        assert isinstance(tour, MemoryTour)
        assert len(tour.steps) > 0
        assert "blast-radius" in tour.tags

        # Every step should have a non-empty context_query
        for step in tour.steps:
            assert step.context_query, f"Step {step.node} has empty context_query"


def test_blast_radius_with_task_description():
    builder = _build_test_graph()

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        builder.save(cache_dir / "builder.pkl")

        from hypergraph_code_explorer.api import HypergraphSession
        session = HypergraphSession(builder, cache_dir=cache_dir)

        task = "introducing CoercionError as a subclass of ValidationError"
        tour = session.blast_radius(
            "ValidationError", depth=1, task_description=task,
        )

        # Task description should appear in context queries
        has_task_mention = any(task in s.context_query for s in tour.steps)
        # At least the default-case steps should include the task
        has_some_task_clause = any(
            "CoercionError" in s.context_query or task in s.context_query
            for s in tour.steps
        )
        assert has_some_task_clause, "Task description not found in any context query"


# ---------------------------------------------------------------------------
# Test 2: generate_analysis_prompt() produces correct output
# ---------------------------------------------------------------------------

def test_generate_analysis_prompt():
    tour = MemoryTour(
        id="test123",
        name="Blast radius: ValidationError",
        summary="test",
        steps=[
            MemoryTourStep(
                node="validate",
                text="validate [calls] -> ValidationError",
                file="validators.py",
                edge_type="CALLS",
                context_query="Would this break if ValidationError changes?",
            ),
            MemoryTourStep(
                node="to_python",
                text="to_python [raises] -> ValidationError",
                file="fields.py",
                edge_type="RAISES",
                context_query="Does this raise or catch ValidationError?",
            ),
        ],
    )

    prompt = generate_analysis_prompt(
        tour, task_description="introduce CoercionError subclass",
    )

    assert "blast radius analysis" in prompt.lower()
    assert "introduce CoercionError subclass" in prompt
    assert "## Tour: Blast radius: ValidationError" in prompt
    assert "### Step 1: validate" in prompt
    assert "### Step 2: to_python" in prompt
    assert "Would this break if ValidationError changes?" in prompt
    assert "Does this raise or catch ValidationError?" in prompt
    assert "validators.py" in prompt
    assert "fields.py" in prompt
    assert "Risk assessment" in prompt
    assert "summary of the complete blast radius" in prompt


def test_generate_analysis_prompt_without_task():
    tour = MemoryTour(
        id="t1", name="Test", summary="",
        steps=[MemoryTourStep(node="x", text="y", context_query="q?")],
    )
    prompt = generate_analysis_prompt(tour)
    assert "Task:" not in prompt
    assert "### Step 1: x" in prompt
    assert "q?" in prompt


# ---------------------------------------------------------------------------
# Test 5: extract_tour_subgraph() with edge_types parameter
# ---------------------------------------------------------------------------

def test_extract_tour_subgraph_edge_types_filter():
    builder = _build_test_graph()

    tour = MemoryTour(
        id="t1", name="Test", summary="",
        keywords=["ValidationError"],
        steps=[MemoryTourStep(node="ValidationError", text="root")],
    )

    # Default: all structural types
    full = extract_tour_subgraph(builder, [tour])
    full_types = {e["type"] for e in full["edges"]}
    assert "CALLS" in full_types
    assert "RAISES" in full_types

    # Filter to only CALLS
    calls_only = extract_tour_subgraph(builder, [tour], edge_types={"CALLS"})
    calls_types = {e["type"] for e in calls_only["edges"]}
    assert calls_types == {"CALLS"}
    assert len(calls_only["edges"]) < len(full["edges"])

    # Filter to only INHERITS
    inherits_only = extract_tour_subgraph(builder, [tour], edge_types={"INHERITS"})
    inherits_types = {e["type"] for e in inherits_only["edges"]}
    assert inherits_types == {"INHERITS"}


def test_extract_tour_subgraph_edge_types_none_uses_default():
    """Passing edge_types=None should use STRUCTURAL_TYPES (same as no arg)."""
    builder = _build_test_graph()

    tour = MemoryTour(
        id="t1", name="Test", summary="",
        keywords=["ValidationError"],
        steps=[MemoryTourStep(node="ValidationError", text="root")],
    )

    default_result = extract_tour_subgraph(builder, [tour])
    none_result = extract_tour_subgraph(builder, [tour], edge_types=None)

    assert len(default_result["edges"]) == len(none_result["edges"])
