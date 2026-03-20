"""
Text / Doc Hyperedge Extractor (Claude-based)
=============================================
Uses the Anthropic API with structured output (instructor + pydantic) to
extract N-ary hyperedges from text chunks.

OPT-IN: This is disabled by default for code repos. Only runs when
text_edges=True is set in config.
"""

from __future__ import annotations

import re
import time
from hashlib import md5

from pydantic import BaseModel, Field

from ..ingestion.chunker import Chunk
from ..models import HyperedgeRecord


# ---------------------------------------------------------------------------
# Pydantic schemas for structured Claude output
# ---------------------------------------------------------------------------

class HyperedgeEvent(BaseModel):
    """A single N-ary relationship event extracted from text."""
    sources: list[str] = Field(
        description="List of source entity strings"
    )
    relation: str = Field(
        description="The relationship predicate (verb phrase)"
    )
    targets: list[str] = Field(
        description="List of target entity strings"
    )
    edge_type: str = Field(
        default="TEXT",
        description="One of: TEXT, API, DEPENDENCY, CONCEPT, SEQUENCE, COMPOSES"
    )


class ExtractionResult(BaseModel):
    """Structured output from the hyperedge extraction prompt."""
    events: list[HyperedgeEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_DISTILL_SYSTEM = (
    "You are a precise technical editor. Rewrite the given text as a clean, "
    "self-contained technical summary that preserves all specific technical terms, "
    "function names, class names, library names exactly as written. "
    "Remove human names, references like '[1]', and footnotes. "
    "Return only the rewritten text."
)

_EXTRACT_SYSTEM = (
    "You are a technical knowledge hypergraph extractor. Extract N-ary relationships "
    "(hyperedges) from technical text.\n\n"
    "Produce HYPEREDGES that connect MULTIPLE entities at once when appropriate.\n\n"
    "EDGE TYPES: TEXT, API, DEPENDENCY, CONCEPT, SEQUENCE, COMPOSES\n\n"
    "RULES:\n"
    "  1. Keep all technical terms exactly as written\n"
    "  2. Prefer specific verbs over generic ones\n"
    "  3. Lists sharing one relationship should be ONE hyperedge\n"
    "  4. Minimum 2 nodes per edge; prefer 3+ when possible\n\n"
    "Return JSON with field 'events' containing a list of hyperedge objects. "
    "Each must have: 'sources' (list), 'relation' (string), 'targets' (list), 'edge_type' (string)."
)

_EXTRACT_USER_TEMPLATE = (
    "Extract all N-ary hyperedges from this technical text:\n\n"
    "```\n{text}\n```\n\n"
    "Return JSON with field 'events'."
)


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------

class TextHyperedgeExtractor:
    """Extracts hyperedges from text chunks using the Claude API. OPT-IN only."""

    def __init__(
        self,
        anthropic_client,
        model: str = "claude-sonnet-4-6",
        do_distill: bool = True,
        max_retries: int = 3,
        verbose: bool = False,
    ):
        self.client = anthropic_client
        self.model = model
        self.do_distill = do_distill
        self.max_retries = max_retries
        self.verbose = verbose

        try:
            import instructor
            self._instructor_client = instructor.from_anthropic(self.client)
            self._use_instructor = True
        except ImportError:
            self._instructor_client = None
            self._use_instructor = False

    def _distill(self, text: str) -> str:
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=1500,
                    system=_DISTILL_SYSTEM,
                    messages=[{"role": "user", "content": text}],
                )
                return response.content[0].text
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    if self.verbose:
                        print(f"  Distill failed: {e}. Using raw text.")
                    return text

    def _extract_with_instructor(self, text: str) -> ExtractionResult:
        for attempt in range(self.max_retries):
            try:
                result = self._instructor_client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=_EXTRACT_SYSTEM,
                    messages=[{
                        "role": "user",
                        "content": _EXTRACT_USER_TEMPLATE.format(text=text),
                    }],
                    response_model=ExtractionResult,
                )
                return result
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    if self.verbose:
                        print(f"  Instructor extraction failed: {e}")
                    return ExtractionResult(events=[])

    def _extract_with_raw_api(self, text: str) -> ExtractionResult:
        import json as json_mod

        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=_EXTRACT_SYSTEM,
                    messages=[{
                        "role": "user",
                        "content": _EXTRACT_USER_TEMPLATE.format(text=text),
                    }],
                )
                raw = response.content[0].text
                json_match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not json_match:
                    return ExtractionResult(events=[])

                data = json_mod.loads(json_match.group())
                events = []
                for ev in data.get("events", []):
                    def to_list(v):
                        return v if isinstance(v, list) else [v]
                    events.append(HyperedgeEvent(
                        sources=to_list(ev.get("sources", ev.get("source", []))),
                        relation=ev.get("relation", ""),
                        targets=to_list(ev.get("targets", ev.get("target", []))),
                        edge_type=ev.get("edge_type", "TEXT"),
                    ))
                return ExtractionResult(events=events)
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    if self.verbose:
                        print(f"  Raw extraction failed: {e}")
                    return ExtractionResult(events=[])

    def _extract(self, text: str) -> ExtractionResult:
        if self._use_instructor:
            return self._extract_with_instructor(text)
        return self._extract_with_raw_api(text)

    def extract(self, chunk: Chunk) -> list[HyperedgeRecord]:
        """Extract HyperedgeRecord objects from a text chunk."""
        if chunk.is_code:
            return []

        if self.verbose:
            label = chunk.heading or chunk.symbol_name or chunk.chunk_id[:8]
            print(f"  Extracting text hyperedges: {label}")

        text = chunk.text
        if self.do_distill and len(text) > 200:
            text = self._distill(text)

        result = self._extract(text)

        edges: list[HyperedgeRecord] = []
        for i, event in enumerate(result.events):
            if not event.sources or not event.targets:
                continue

            sources = [s.strip() for s in event.sources if s.strip()]
            targets = [t.strip() for t in event.targets if t.strip()]
            if len(sources) + len(targets) < 2:
                continue

            eid = md5(
                f"TEXT_{event.relation[:30]}_{chunk.chunk_id[:8]}_{i}".encode()
            ).hexdigest()[:16]

            edges.append(HyperedgeRecord(
                edge_id=eid,
                relation=event.relation,
                edge_type=event.edge_type,
                sources=sources,
                targets=targets,
                source_path=chunk.source_path,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
                metadata={"heading": chunk.heading},
            ))

        return edges

    def extract_all(self, chunks: list[Chunk], skip_code: bool = True) -> list[HyperedgeRecord]:
        edges: list[HyperedgeRecord] = []
        for chunk in chunks:
            if skip_code and chunk.is_code:
                continue
            edges.extend(self.extract(chunk))
        return edges
