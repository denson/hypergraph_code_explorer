"""Tests for memory tours: data model, sidecar persistence, scaffolding."""

from __future__ import annotations

import json

import pytest

from hypergraph_code_explorer.memory_tours import (
    MemoryTour,
    MemoryTourStep,
    MemoryTourStore,
    scaffold_from_plan,
    scaffold_prompt,
)


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------

class TestMemoryTourStep:
    def test_roundtrip(self):
        step = MemoryTourStep(node="Session.send", text="sends request", file="s.py", edge_type="CALLS")
        d = step.to_dict()
        step2 = MemoryTourStep.from_dict(d)
        assert step2.node == "Session.send"
        assert step2.text == "sends request"
        assert step2.file == "s.py"
        assert step2.edge_type == "CALLS"

    def test_optional_fields_omitted(self):
        step = MemoryTourStep(node="X", text="desc")
        d = step.to_dict()
        assert "file" not in d
        assert "edge_type" not in d


class TestMemoryTour:
    def test_auto_id_and_timestamp(self):
        tour = MemoryTour(id="", name="t", summary="s")
        assert len(tour.id) == 12
        assert tour.created_at != ""

    def test_explicit_id_preserved(self):
        tour = MemoryTour(id="abc123", name="t", summary="s")
        assert tour.id == "abc123"

    def test_roundtrip(self):
        tour = MemoryTour(
            id="test01",
            name="Auth Flow",
            summary="How auth works",
            keywords=["Session", "Auth"],
            steps=[MemoryTourStep(node="Session", text="entry")],
            tags=["auth"],
            created_from_query="how does auth work",
            promoted=True,
            use_count=3,
        )
        d = tour.to_dict()
        tour2 = MemoryTour.from_dict(d)
        assert tour2.id == "test01"
        assert tour2.name == "Auth Flow"
        assert tour2.promoted is True
        assert tour2.use_count == 3
        assert len(tour2.steps) == 1
        assert tour2.steps[0].node == "Session"

    def test_touch(self):
        tour = MemoryTour(id="t1", name="t", summary="s")
        assert tour.use_count == 0
        assert tour.last_used_at == ""
        tour.touch()
        assert tour.use_count == 1
        assert tour.last_used_at != ""


# ---------------------------------------------------------------------------
# Sidecar persistence tests
# ---------------------------------------------------------------------------

class TestMemoryTourStore:
    def test_add_and_list(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        assert len(store) == 0

        tour = MemoryTour(id="", name="T1", summary="S1", tags=["x"])
        store.add(tour)
        assert len(store) == 1

        tours = store.list_tours()
        assert len(tours) == 1
        assert tours[0].name == "T1"

    def test_persistence_roundtrip(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        tour = MemoryTour(id="abc", name="T1", summary="S1")
        store.add(tour)

        store2 = MemoryTourStore(tmp_path)
        assert len(store2) == 1
        assert store2.get("abc") is not None
        assert store2.get("abc").name == "T1"

    def test_sidecar_file_format(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        store.add(MemoryTour(id="x", name="N", summary="S"))

        raw = json.loads((tmp_path / "memory_tours.json").read_text())
        assert raw["version"] == 1
        assert len(raw["tours"]) == 1
        assert raw["tours"][0]["id"] == "x"

    def test_remove(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        store.add(MemoryTour(id="a", name="A", summary="SA"))
        store.add(MemoryTour(id="b", name="B", summary="SB"))
        assert len(store) == 2

        assert store.remove("a") is True
        assert len(store) == 1
        assert store.get("a") is None

        assert store.remove("nonexistent") is False

    def test_promote(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        store.add(MemoryTour(id="a", name="A", summary="S"))
        assert store.get("a").promoted is False

        result = store.promote("a")
        assert result.promoted is True
        assert store.get("a").promoted is True

        store2 = MemoryTourStore(tmp_path)
        assert store2.get("a").promoted is True

    def test_promote_nonexistent(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        assert store.promote("nope") is None

    def test_touch(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        store.add(MemoryTour(id="a", name="A", summary="S"))
        assert store.get("a").use_count == 0

        store.touch("a")
        assert store.get("a").use_count == 1

    def test_filter_by_tag(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        store.add(MemoryTour(id="a", name="A", summary="S", tags=["auth"]))
        store.add(MemoryTour(id="b", name="B", summary="S", tags=["db"]))

        auth_tours = store.list_tours(tag="auth")
        assert len(auth_tours) == 1
        assert auth_tours[0].id == "a"

    def test_filter_promoted_only(self, tmp_path):
        store = MemoryTourStore(tmp_path)
        store.add(MemoryTour(id="a", name="A", summary="S", promoted=True))
        store.add(MemoryTour(id="b", name="B", summary="S", promoted=False))

        promoted = store.list_tours(promoted_only=True)
        assert len(promoted) == 1
        assert promoted[0].id == "a"

    def test_empty_cache_dir_created(self, tmp_path):
        new_dir = tmp_path / "sub" / "cache"
        store = MemoryTourStore(new_dir)
        store.add(MemoryTour(id="a", name="A", summary="S"))
        assert (new_dir / "memory_tours.json").exists()


# ---------------------------------------------------------------------------
# Scaffold tests
# ---------------------------------------------------------------------------

class _FakePlan:
    """Minimal stand-in for RetrievalPlan for testing scaffold functions."""
    def __init__(self, query="test query", related_symbols=None,
                 primary_files=None, classification=None,
                 structural_context=""):
        self.query = query
        self.related_symbols = related_symbols or []
        self.primary_files = primary_files or []
        self.classification = classification or []
        self.structural_context = structural_context


class _FakeSym:
    def __init__(self, name, relationship="", targets=None, file="", edge_type=""):
        self.name = name
        self.relationship = relationship
        self.targets = targets or []
        self.file = file
        self.edge_type = edge_type


class _FakeFile:
    def __init__(self, path, symbols=None, reason=""):
        self.path = path
        self.symbols = symbols or []
        self.reason = reason


class TestScaffoldFromPlan:
    def test_basic_scaffold(self):
        plan = _FakePlan(
            query="how does auth work",
            related_symbols=[
                _FakeSym("Session", relationship="calls", targets=["Auth.check"]),
                _FakeSym("Auth.check", relationship="defines", file="auth.py"),
            ],
        )
        tour = scaffold_from_plan(plan)
        assert tour.name == "Tour: how does auth work"
        assert tour.created_from_query == "how does auth work"
        assert len(tour.steps) == 2
        assert tour.steps[0].node == "Session"
        assert "calls" in tour.steps[0].text
        assert tour.promoted is False
        assert len(tour.keywords) == 2

    def test_custom_name_and_tags(self):
        plan = _FakePlan(query="q")
        tour = scaffold_from_plan(plan, name="My Tour", tags=["important"])
        assert tour.name == "My Tour"
        assert tour.tags == ["important"]

    def test_file_symbols_added(self):
        plan = _FakePlan(
            query="q",
            related_symbols=[_FakeSym("A")],
            primary_files=[_FakeFile("f.py", symbols=["B", "A"])],
        )
        tour = scaffold_from_plan(plan)
        nodes = [s.node for s in tour.steps]
        assert "A" in nodes
        assert "B" in nodes
        assert len(tour.steps) == 2  # A from symbols, B from files (A deduped)

    def test_empty_plan(self):
        plan = _FakePlan(query="nothing")
        tour = scaffold_from_plan(plan)
        assert len(tour.steps) == 0
        assert tour.name == "Tour: nothing"


class TestScaffoldPrompt:
    def test_contains_query_and_schema(self):
        plan = _FakePlan(
            query="how does middleware work",
            related_symbols=[_FakeSym("Middleware", relationship="calls")],
        )
        prompt = scaffold_prompt(plan)
        assert "how does middleware work" in prompt
        assert '"name"' in prompt
        assert '"steps"' in prompt
        assert "Middleware" in prompt

    def test_includes_existing_tours(self):
        plan = _FakePlan(query="q")
        prompt = scaffold_prompt(plan, existing_tour_names=["Auth Flow", "DB Layer"])
        assert "Auth Flow" in prompt
        assert "DB Layer" in prompt

    def test_no_existing_tours(self):
        plan = _FakePlan(query="q")
        prompt = scaffold_prompt(plan)
        assert "(none)" in prompt
