"""Tests for RetrievalPlan data model and formatters."""

from __future__ import annotations

from hypergraph_code_explorer.retrieval.plan import (
    FileSuggestion,
    GrepSuggestion,
    Overview,
    RetrievalPlan,
    SymbolRelation,
    format_json,
    format_text,
)


def test_plan_is_empty():
    plan = RetrievalPlan(query="test")
    assert plan.is_empty()


def test_plan_not_empty_with_files():
    plan = RetrievalPlan(
        query="test",
        primary_files=[FileSuggestion(path="foo.py")],
    )
    assert not plan.is_empty()


def test_plan_not_empty_with_context():
    plan = RetrievalPlan(query="test", structural_context="some context")
    assert not plan.is_empty()


def test_plan_merge_deduplicates_files():
    plan1 = RetrievalPlan(
        query="test",
        tiers_used=[1],
        primary_files=[FileSuggestion(path="a.py")],
    )
    plan2 = RetrievalPlan(
        query="test",
        tiers_used=[2],
        primary_files=[
            FileSuggestion(path="a.py"),  # duplicate
            FileSuggestion(path="b.py"),
        ],
    )
    plan1.merge(plan2)
    assert len(plan1.primary_files) == 2
    paths = {f.path for f in plan1.primary_files}
    assert paths == {"a.py", "b.py"}


def test_plan_merge_combines_tiers():
    plan1 = RetrievalPlan(query="test", tiers_used=[1])
    plan2 = RetrievalPlan(query="test", tiers_used=[2, 3])
    plan1.merge(plan2)
    assert plan1.tiers_used == [1, 2, 3]


def test_plan_merge_appends_context():
    plan1 = RetrievalPlan(query="test", structural_context="first")
    plan2 = RetrievalPlan(query="test", structural_context="second")
    plan1.merge(plan2)
    assert "first" in plan1.structural_context
    assert "second" in plan1.structural_context


def test_plan_to_dict_roundtrip():
    plan = RetrievalPlan(
        query="test query",
        classification=["identifier"],
        tiers_used=[1],
        primary_files=[FileSuggestion(path="foo.py", symbols=["Foo"], reason="test")],
        grep_suggestions=[GrepSuggestion(pattern="Foo", reason="find it")],
        related_symbols=[SymbolRelation(name="Foo", relationship="defines")],
        structural_context="Found Foo",
    )
    d = plan.to_dict()
    assert d["query"] == "test query"
    assert len(d["primary_files"]) == 1
    assert d["primary_files"][0]["path"] == "foo.py"
    assert len(d["grep_suggestions"]) == 1
    assert len(d["related_symbols"]) == 1


def test_format_text_includes_sections():
    plan = RetrievalPlan(
        query="Session.send",
        tiers_used=[1],
        primary_files=[FileSuggestion(path="sessions.py", symbols=["Session"], priority=1)],
        grep_suggestions=[GrepSuggestion(pattern="send", reason="find usages")],
        related_symbols=[SymbolRelation(name="Session", relationship="defines", edge_type="DEFINES")],
        structural_context="Found 1 node",
    )
    text = format_text(plan)
    assert "Files to Read" in text
    assert "sessions.py" in text
    assert "Grep Suggestions" in text
    assert "send" in text
    assert "Related Symbols" in text
    assert "Context" in text


def test_format_text_empty_plan():
    plan = RetrievalPlan(query="nothing")
    text = format_text(plan)
    assert "No results found" in text


def test_format_json_valid():
    import json
    plan = RetrievalPlan(
        query="test",
        primary_files=[FileSuggestion(path="a.py")],
    )
    result = format_json(plan)
    parsed = json.loads(result)
    assert parsed["query"] == "test"
    assert len(parsed["primary_files"]) == 1


def test_file_suggestion_to_dict():
    fs = FileSuggestion(path="foo.py", symbols=["A", "B"], reason="test", priority=2)
    d = fs.to_dict()
    assert d["path"] == "foo.py"
    assert d["symbols"] == ["A", "B"]
    assert d["priority"] == 2


def test_symbol_relation_to_dict():
    sr = SymbolRelation(
        name="Foo", file="foo.py", relationship="calls",
        edge_type="CALLS", targets=["Bar", "Baz"],
    )
    d = sr.to_dict()
    assert d["name"] == "Foo"
    assert d["targets"] == ["Bar", "Baz"]
