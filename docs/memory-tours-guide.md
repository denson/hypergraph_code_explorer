# Memory Tours — Structured Agent Memory

Memory tours are general-purpose structured memory for LLM agents working with a codebase. They persist across sessions, carry provenance, and support multi-agent annotation. This guide explains how to think about tours as a memory primitive — not a fixed set of task recipes, but a tool for managing cognitive state.

## 1. What tours are

A memory tour is a named, tagged, timestamped collection of graph nodes with free-text annotations. Each tour lives in `.hce_cache/memory_tours.json` alongside the graph index.

A tour carries:

- **Steps** — graph nodes (symbols, files, types) with text describing what each one means in context
- **Provenance** — the query that created it, when it was created, who created it
- **Reuse tracking** — how many times it has been recalled, when it was last used
- **Promotion status** — whether it is ephemeral working notes or durable institutional knowledge
- **Tags** — labels that group tours into views
- **Annotations** — layered notes from multiple agents, at both tour and step level

Tours are cheap. Creating a tour is a single API call. There is no step limit — a 118-step tour is as readable as a 4-step tour because the format is uniform structured data. Create tours freely; promote the ones that earn it; discard the rest.

### What tours are not

**Not reduced-size proxies for the graph.** The full graph for a typical codebase is under 1MB of structured JSON. An LLM can traverse it directly. Tours do not exist because the graph is too large — they exist because raw structure lacks interpretation.

**Not predefined task types.** There is no fixed taxonomy of tour kinds. Any question, any concern, any perspective can become a tour. The examples in this guide are illustrative, not prescriptive.

**Not owned by any one agent.** Any agent that loads the same `.hce_cache` directory sees all tours. Tours are shared memory. Conventions (see Section 6) keep them legible across agents.

## 2. Views — tours as parallel lenses

A **view** is a set of tours sharing a tag. It represents one agent's perspective on the codebase.

Consider three agents working on the same FastAPI codebase simultaneously:

- Agent A is reviewing security. It tags its tours `security`. Its tours capture auth flows, permission checks, credential handling, token validation chains.
- Agent B is profiling performance. It tags its tours `perf`. Its tours capture hot paths, allocation sites, I/O boundaries, serialization bottlenecks.
- Agent C is onboarding a new developer. It tags its tours `onboarding`. Its tours capture entry points, key abstractions, a recommended reading order.

Each agent sees the others' tours but works within its own tag namespace. The views coexist without conflict because tours are append-only — creating a tour never modifies or removes existing ones.

**Tags are the namespace mechanism.** Use them to delineate views. An agent starting work on a concern should list existing tours for that tag first, absorb what's there, then build on it rather than duplicating work.

Tag conventions:

- Use a primary tag for the agent's concern: `security`, `perf`, `onboarding`, `refactor`
- Use issue or task IDs for scoped work: `bug-1234`, `migration-v3`, `pr-567`
- Use cross-cutting tags when a tour is relevant to multiple views: a tour tagged `security, auth` belongs to the security view but is discoverable by anyone searching for auth

## 3. Tour lifecycle

Four phases. These are heuristics, not rigid rules.

### Create

Create a tour when you've learned something that would be useful later — to you or to another agent. Two creation paths:

**Auto-scaffold** — run a graph query, then create a tour from the result. The tour steps mirror the query output: each step is a graph node with machine-generated relationship text.

```
plan = s.query('how does authentication work', depth=2)
tour = s.memory_tour_create(plan, name='Auth flow overview', tags=['security', 'auth'])
```

**Hand-craft** — build a tour from scratch as a JSON dict. Use this when the structural query is a starting point but the tour needs to tell a story with the agent's own interpretation.

```
s.memory_tour_create_from_dict({
    "name": "Auth middleware chain",
    "summary": "Request authentication flows through three middleware layers before reaching the route handler.",
    "keywords": ["OAuth2", "HTTPBearer", "SecurityBase"],
    "tags": ["security", "auth"],
    "steps": [
        {"node": "oauth2.OAuth2.__call__", "text": "Entry point: extracts bearer token from Authorization header"},
        {"node": "SecurityBase", "text": "Base class — all auth schemes inherit from this and implement __call__"},
        {"node": "http.HTTPBearer.__call__", "text": "Validates token format before passing to the route's dependency"}
    ]
})
```

### Discover

When starting a task, check what memory already exists before querying the graph.

```
tours = s.memory_tour_list(tag='security')           # all security tours
tours = s.memory_tour_list(promoted_only=True)        # only promoted (verified) tours
tour = s.memory_tour_get(tour_id)                     # recall a specific tour (increments use_count)
```

Read summaries first. A tour's `summary` field should tell you whether it's relevant without requiring you to read every step. If a tour is relevant, recall it with `memory_tour_get` — this records the usage, which informs future promotion and cleanup decisions.

### Promote

Promote a tour when it has proven useful — you've recalled it and it helped, or it captures something durably true about the architecture that future agents should know.

```
s.memory_tour_promote(tour_id)
```

Promotion is a signal: "I'm confident this is correct and durably useful." Promoted tours survive cleanup and are discoverable via `promoted_only=True`. Unpromoted tours are working notes — possibly wrong, possibly incomplete, possibly no longer relevant.

### Discard

Remove ephemeral tours when the task they served is complete. Don't accumulate dead memory.

```
s.memory_tour_remove(tour_id)
```

A tour with `use_count: 0` and `promoted: false` that is older than the current task is a candidate for removal.

## 4. Annotations

Tours are not write-once artifacts. Any agent can annotate an existing tour — at both the tour level and the individual step level — without overwriting what's already there. Annotations are how tours accumulate perspectives from multiple agents.

### Annotation structure

Each annotation is a timestamped note with agent identity:

```json
{
    "author": "security-reviewer",
    "text": "Verified: all auth paths eventually check token validity before granting access",
    "created_at": "2026-03-25T10:00:00Z"
}
```

Both `MemoryTour` and `MemoryTourStep` carry an `annotations` list. Annotations append — they never overwrite each other or the original tour content.

### Tour-level annotations

Tour-level annotations capture conclusions, status, or caveats about the tour as a whole:

- "This tour is incomplete — couldn't find the error handling path from middleware to exception handler"
- "Verified against v3.2.0 source — still accurate"
- "Superseded by tour 'Auth Flow v2' (id: abc123)"
- "Covers the happy path only — see tour 'Auth Error Handling' for failure modes"

### Step-level annotations

Step-level annotations capture observations about individual nodes:

- "This is where the 500 error originates — field validation fails on malformed input"
- "Hot path — called 10k times per request in load test"
- "This pattern is fragile — breaks if the base class changes its __init__ signature"
- "The actual implementation delegates to Starlette here — FastAPI's version is a thin wrapper"

### When to annotate vs. create a new tour

**Annotate** when you are adding interpretation to an existing structural finding. The tour's steps already identify the right nodes — you are saying what you think about them.

**Create a new tour** when you are asking a different question or exploring a different part of the graph. The new tour identifies different nodes.

Put simply: annotations say "here's what I think about what you found." New tours say "I looked somewhere else."

### Annotation during creation

When using `memory_tour_create_from_dict`, the `text` field on each step is the primary annotation — it's where the creating agent puts its interpretation. This is the most common case: the agent queries the graph, understands what it sees, and creates a tour with interpreted step text.

When auto-scaffolding with `memory_tour_create`, the step text is machine-generated relationship descriptions (e.g., `"routing [imports] -> starlette.websockets"`). These are structural facts, not interpretations. The creating agent should add annotations afterward to layer its understanding on top.

### Post-creation annotation

An agent reads an existing tour, understands it, then adds its own notes:

```
s.memory_tour_annotate(tour_id, author='perf-analyzer', text='Steps 5-8 are in the hot path — N+1 query risk')
s.memory_tour_annotate_step(tour_id, step_index=5, author='perf-analyzer', text='This call happens inside a loop over all dependencies')
```

These operations append to the annotations list. The original tour content and all previous annotations remain untouched.

### Multi-agent annotation in practice

This is what makes tours genuinely collaborative:

1. **Agent A** (architect) creates a tour of the request lifecycle with 15 steps, tagging it `architecture`.
2. **Agent B** (security reviewer) reads the tour, adds a tour-level annotation: "Auth check happens at step 3 but is bypassable if middleware order changes." Adds step-level annotations on steps 3 and 7.
3. **Agent C** (performance profiler) reads the same tour, adds step-level annotations on steps 5, 8, and 12 marking them as hot paths with measured latency data.

The tour now has three layers of perspective. A future agent reading it gets the structural map (Agent A), the security assessment (Agent B), and the performance profile (Agent C) in a single artifact.

## 5. Composition

An agent's view of the codebase is built incrementally, not in one shot. The pattern:

1. **Broad query** — ask a high-level question, create a tour from the result. This is the scaffolding.
2. **Targeted follow-ups** — identify gaps in the first tour, run focused queries, create additional tours. Each one fills in a facet.
3. **Tag unification** — all tours share the same primary tag. The tag is the view.
4. **Annotation layering** — as understanding deepens, annotate existing tours rather than creating redundant new ones.

### One tour per question

The heuristic for when to create one big tour vs. multiple small ones: **one tour per question or concern.** If you asked "how does authentication work?" that's one tour. If you then asked "what calls the token validator?" that's a second tour. They share a tag, but each captures a distinct line of inquiry.

This keeps tours focused and their summaries meaningful. A tour that tries to capture everything about security is harder to summarize and harder for another agent to evaluate than five tours that each capture one security concern.

### Keywords and summaries

Tours are discovered by listing and scanning summaries. Invest in making them self-describing:

- **Summary**: A complete sentence stating what the tour captures. Good: "Request authentication flows through OAuth2.__call__ to SecurityBase, with three auth scheme implementations branching at HTTPBearer." Bad: "Auth stuff."
- **Keywords**: The key symbol names that appear in the tour. These help with search and relevance matching.
- **Name**: A short descriptive title. Good: "Auth middleware chain from request to token validation." Bad: "Tour 3."

### Auto-scaffolded vs. hand-crafted tours

Auto-scaffolded tours (`memory_tour_create`) are fast to produce and structurally accurate — every step is a real graph node with a real relationship. But the step text is machine-generated and lacks interpretation.

Hand-crafted tours (`memory_tour_create_from_dict`) take more effort but carry the agent's understanding. Each step's text explains *why* the node matters, not just *what* it connects to.

The practical pattern: auto-scaffold first for speed, then either annotate the auto-scaffolded tour or create a hand-crafted replacement when the interpretation matters enough to justify the effort.

## 6. Cross-agent conventions

Any agent loading the `.hce_cache` sees all tours. These conventions keep the shared space legible.

### Tag discipline

One primary tag per view. Additional tags for cross-cutting concerns. Don't over-tag — two or three tags per tour is typical.

### Naming

Tour names should be self-explanatory without requiring the steps to be read. The name appears in `memory_tour_list` output and is often the only thing another agent sees before deciding whether to recall the full tour.

### Summaries

The `summary` field is the first thing another agent reads after the name. Make it a complete sentence stating what architectural insight the tour captures. Include the key structural claim — not just the topic, but what the tour says about the topic.

### Promotion signals

- **Promoted** = "I'm confident this is correct and durably useful." Treat promoted tours as reliable unless evidence contradicts them.
- **Unpromoted** = "Working notes. May be wrong, incomplete, or no longer relevant." Treat unpromoted tours as leads to investigate, not established facts.

### Annotation authorship

Always set the `author` field on annotations. Use a descriptive identifier for the agent's role or task — `security-reviewer`, `perf-profiler`, `bug-1234-investigator` — not a generic name like `agent` or `assistant`. This tells future readers which perspective the annotation comes from and how much weight to give it for a particular concern.

### Stale tour handling

If you discover a tour that is no longer accurate — the code has changed since it was created — you have three options:

1. **Annotate** the tour noting it is stale and what has changed
2. **Create a replacement** tour and annotate the old one pointing to the new one
3. **Remove** the old tour if it has no promoted status and no annotations from other agents

Prefer option 1 or 2 over option 3. Stale tours with annotations still carry useful history about how the codebase used to work and what other agents observed.

## 7. The full graph as memory

The full graph can be loaded as JSON and traversed directly. For a typical codebase this is under 1MB of uniform `{type, relation, sources, targets, file}` tuples — one of the easiest formats for an LLM to parse.

Tours exist alongside this capability, not as a replacement for it.

**Use the full graph** when you need the complete structural picture — all call chains, all inheritance trees, all imports. Load it, traverse it, answer structural questions directly.

**Use tours** when you need a curated, annotated, persistent perspective. Tours add three things the raw graph lacks:

1. **Interpretation** — step text and annotations explain *why* a structural relationship matters, not just that it exists
2. **Persistence across sessions** — a tour created by one agent is available to every future agent that loads the same cache
3. **Provenance and trust signals** — you know who created it, when, from what query, whether it's been promoted, how many times it's been used, and what other agents have annotated on it

The raw graph is ground truth. Tours are shared working memory built on top of that truth.

## 8. Visualization

Memory tours are the single canonical format for both agent memory and human-readable visualization. The `visualization.py` module converts memory tours to the D3 viz format automatically and generates both an interactive HTML file and a markdown report.

### How it works

The `hce visualize` CLI command (and `HypergraphSession.visualize()` API method) performs three steps:

1. **Select tours** — filter by tag (`--tags security,auth`) or tour ID (`--tours id1,id2`). If neither is specified, all tours are included.
2. **Extract focused subgraph** — collect seed nodes from tour steps and keywords, find all edges touching those seeds (1-hop neighborhood via the builder's inverted index), compute degree/importance with seed-node boosting so tour-relevant symbols are visually prominent.
3. **Generate outputs** — write a self-contained D3 HTML (interactive force-directed graph with tour sidebar) and a markdown report (tour index tables, step listings, tag distribution).

### Format mapping

The conversion from memory tours to the viz template format is mechanical:

- `steps[].node` → viz step `node` (already matches the node ID scheme)
- `steps[].text` → viz step `text` with symbol names wrapped in `<strong class='tc'>` tags for dynamic coloring in the sidebar
- Tour colors are auto-assigned from a 12-color palette by position (no manual `color` field needed)
- `keywords` for the viz highlight system are derived from tour step nodes and the tour's keyword list
- Group colors are derived from file path directory components via deterministic hash-to-hue mapping (no hardcoded mapping table)

### Usage

```bash
# Visualize all tours
hce visualize --output codebase_viz --title "My Project"

# Visualize security-related tours only
hce visualize --tags security,auth --output security_viz --title "Security Architecture"

# Visualize a single tour by ID
hce visualize --tours 1e9746af6287 --output token_chain
```

Each command writes `<output>.html` and `<output>.md` side-by-side.

### Programmatic API

```python
session = HypergraphSession.load('.hce_cache')
result = session.visualize(tags=['security'], output='security_viz', title='Security')
# result = {"html": "security_viz.html", "md": "security_viz.md", "tours": 2, "nodes": 199, "edges": 316}
```

### Scale

Focused subgraphs are better for humans than full-graph dumps:

| Selection | Tours | Nodes | Edges | HTML Size |
|-----------|------:|------:|------:|----------:|
| 2 security tours | 2 | 199 | 316 | ~118 KB |
| 2 bug tours | 2 | 320 | 429 | ~158 KB |
| All 8 FastAPI tours | 8 | 1,275 | 3,289 | ~712 KB |
| Full graph (no filter) | — | 1,306 | 1,083 | — |

### Future directions

- **Annotations in the sidebar** — step-level and tour-level annotations could appear as sub-items under each step, showing author and text
- **Promotion badges** — promoted tours and use counts could appear as visual badges on tour buttons in the sidebar
- **Tag-based color assignment** — instead of positional colors, derive tour colors from tag names for consistent coloring across regeneration
