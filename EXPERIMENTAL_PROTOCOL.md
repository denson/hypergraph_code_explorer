# Experimental Protocol: HCE Token Efficiency and Answer Quality

Draft v0.1 — for discussion between Denson and Manuel

## Research Question

Does precomputed hypergraph structure (via HCE) allow an LLM-based coding agent to answer structural comprehension questions and localize bugs with fewer tokens and equal or better accuracy, compared to the same agent without the tool?

## Design Principle

Same LLM. Same harness. Same prompt. The only variable is the availability of HCE. Every question has a deterministically verifiable correct answer — no subjective evaluation of code quality.

## Experimental Conditions

Each task is run under two conditions:

- **Control** — The agent has standard file access (read, grep, glob). No HCE.
- **HCE** — The agent has the same file access plus `hce lookup`, `hce search`, `hce query`, `hce overview`, and `hce stats`.

Both conditions use the same system prompt, the same model (Claude Sonnet or Opus — pick one and hold it constant), and the same temperature (0 for reproducibility, or a fixed seed if the API supports it). The only difference is that the HCE condition includes the tool definitions and a pre-built index (`.hce_cache/`) for the repo.

## Repo Selection

Eight repos across six languages and three size tiers. Every repo is well-known, actively maintained, and has clear bug-fix history via GitHub issues and PRs.

### Small (< 1,000 expected HCE nodes)

| Repo | Language | Why |
|------|----------|-----|
| [requests](https://github.com/psf/requests) | Python | HTTP library. Clean module boundaries. Our existing benchmark (906 nodes, 485 edges). Small enough that HCE may not help — that's an honest data point. |
| [cobra](https://github.com/spf13/cobra) | Go | CLI framework used by Kubernetes, Hugo, GitHub CLI. Clear command/flag registration patterns. Good Go-language coverage. |

### Medium (1,000–5,000 expected HCE nodes)

| Repo | Language | Why |
|------|----------|-----|
| [FastAPI](https://github.com/fastapi/fastapi) | Python | Our existing benchmark (1,264 nodes, 1,214 edges). Dependency injection, middleware chains, routing — non-trivial call graph for its size. |
| [tokio](https://github.com/tokio-rs/tokio) | Rust | Async runtime. Complex scheduling, I/O drivers, task management. Exercises Rust tree-sitter support on real-world patterns (traits, lifetimes, macros). |
| [express](https://github.com/expressjs/express) | JavaScript | The canonical Node.js web framework. Middleware pipeline, router, request lifecycle. Extensive bug-fix history. Small-medium codebase but deep call chains. |

### Large (5,000+ expected HCE nodes)

| Repo | Language | Why |
|------|----------|-----|
| [Django](https://github.com/django/django) | Python | Our existing benchmark (23,614 nodes, 19,382 edges). Large enough that navigating without structure is painful. This is where HCE should show its biggest advantage. |
| [Spring Framework](https://github.com/spring-projects/spring-framework) | Java | Enterprise framework. Dependency injection, AOP, lifecycle management, and 20+ submodules. Exercises Java tree-sitter support. Extensive issue/PR history. |
| [Node.js](https://github.com/nodejs/node) | C++ / JavaScript | Multi-language. C++ core (V8 bindings, libuv) with JavaScript standard library. Tests cross-language hypergraph construction. Complex binding patterns between the two layers. |

### Language Coverage

| Language | Repos |
|----------|-------|
| Python | requests, FastAPI, Django |
| Go | cobra |
| Rust | tokio |
| JavaScript | express, Node.js |
| Java | Spring Framework |
| C++ | Node.js |

Six of the ten supported languages. Node.js provides multi-language coverage (C++ + JS). The remaining four (TypeScript, Ruby, PHP, C) are covered by tree-sitter but not evaluated — acknowledge this as a limitation.

## Task Types

### Task Type 1: Structural Comprehension

Questions about the codebase that have objectively verifiable answers. The agent responds in natural language; we check whether the answer contains the correct information.

**Question templates** (adapt per repo):

1. **Direct dependency** — "What functions does `X` call directly?" Ground truth: extract from the actual source of X.
2. **Reverse dependency** — "What calls function `X`?" Ground truth: grep for all call sites of X across the codebase.
3. **Transitive dependency** — "Trace the call chain from `X` to `Y`." Ground truth: manually verify a valid path exists in the code.
4. **Module boundary** — "Which modules does the `<subsystem>` depend on?" Ground truth: analyze imports across the subsystem's files.
5. **Inheritance** — "What is the class hierarchy rooted at `X`?" Ground truth: trace `extends`/`inherits` declarations.
6. **Entry point** — "What are the public entry points for `<feature>`?" Ground truth: identify exported/public functions that initiate the feature.

For each repo, write 8–12 questions spanning these templates. Target questions that require looking at 3+ files to answer correctly — these are the cases where structure helps.

**Scoring:**

- **Correct** (1.0) — Answer identifies the right symbols/files/relationships. Minor omissions of low-relevance items are fine.
- **Partially correct** (0.5) — Answer includes some correct information but misses key symbols or includes incorrect relationships.
- **Incorrect** (0.0) — Answer is wrong or fails to address the question.

Two evaluators score independently. Resolve disagreements by discussion. Report inter-rater agreement (Cohen's kappa).

### Task Type 2: Historical Bug Localization

For each repo, select 5 bugs from the git history that meet these criteria:

- The bug was reported as a GitHub issue
- A fix was merged as a PR that modifies a specific, identifiable set of files and functions
- The bug requires understanding structural relationships to locate (not a typo or off-by-one in a single function)
- The bug existed for at least a few days (not caught immediately), suggesting it wasn't trivially obvious

**Procedure:**

1. Check out the commit immediately before the fix
2. Build the HCE index at that commit (for the HCE condition)
3. Present the agent with the bug report text (from the GitHub issue)
4. Ask: "Based on this bug report, identify the file(s) and function(s) most likely responsible."
5. Compare the agent's answer to the actual files/functions modified in the fix PR

**Scoring:**

- **Correct** (1.0) — Agent identifies at least one of the actual files AND at least one of the actual functions modified in the fix.
- **Partially correct** (0.5) — Agent identifies the correct file(s) but wrong function(s), or identifies a closely related function in the right area.
- **Incorrect** (0.0) — Agent points to the wrong part of the codebase.

## Measurements

For every task execution, record:

| Metric | How |
|--------|-----|
| **Input tokens** | Sum of all tokens sent to the model across all turns |
| **Output tokens** | Sum of all tokens generated by the model across all turns |
| **Total tokens** | Input + output |
| **Tool calls** | Number of tool invocations (file reads, greps, HCE queries) |
| **Files read** | Count of distinct files the agent opened |
| **Turns** | Number of agent reasoning steps / tool-use cycles |
| **Accuracy** | Score per the rubrics above |
| **Wall-clock time** | Start to final answer (informational, not a primary metric) |

## Runs and Statistical Design

Each (repo × task × condition) combination is run **5 times** to account for LLM non-determinism. Report mean and standard deviation for all metrics.

**Total runs estimate:**

- 8 repos × ~13 tasks per repo (8 comprehension + 5 bug localization) × 2 conditions × 5 runs = **1,040 runs**

This is substantial. A pilot phase (see below) reduces the risk of wasting runs on a broken protocol.

**Statistical tests:** Paired comparisons (same task, same repo, HCE vs. control). Use Wilcoxon signed-rank test for token counts (likely non-normal distribution). Report effect sizes.

## Pilot Phase

Before the full evaluation, run a pilot on 2 repos (one small, one large — requests and Django) with 3 tasks each and 3 runs per condition. Purpose:

1. Validate that the harness works end-to-end
2. Calibrate question difficulty — if the control condition gets everything right easily, the questions are too simple
3. Estimate variance to confirm that 5 runs is sufficient
4. Check that HCE indexing works cleanly on the repo at the historical commit

Adjust the protocol based on pilot findings before scaling to all 8 repos.

## Harness Specification

The harness is a script that:

1. Sets up a clean environment for each run (fresh conversation, no carry-over)
2. Loads the system prompt (identical for both conditions, except the HCE condition includes tool definitions)
3. Sends the task prompt
4. Lets the agent run to completion (tool calls, reasoning, final answer)
5. Logs all API traffic: tokens, tool calls, file reads, wall-clock time
6. Extracts the agent's final answer for scoring

The harness must be deterministic in everything except the LLM's own sampling. Same model version, same API parameters, same file system state (checked out at the same commit).

**Open question:** Which API to use? Claude Code's `/api` mode gives realistic tool-use behavior. Alternatively, raw Anthropic API with tool definitions gives more control over logging. Decide during pilot.

## What We Expect to Find

Be explicit about hypotheses so we can't unconsciously move the goalposts:

- **H1 (Token efficiency):** HCE reduces total tokens by 50%+ on large repos (Django, Spring, Node.js) and 20%+ on medium repos. On small repos, the difference may be negligible or even negative (HCE tool definitions add overhead).
- **H2 (Accuracy):** HCE achieves equal or higher accuracy on structural comprehension. The advantage should increase with repo size.
- **H3 (Bug localization):** HCE improves bug localization accuracy on bugs that involve cross-module interactions. For bugs localized to a single file, the advantage is minimal.
- **H4 (File reads):** HCE reduces the number of files read by 60%+ on large repos. The agent reads targeted files instead of scanning broadly.

If any hypothesis is not supported, report that honestly. Negative results on small repos actually strengthen the paper — they show we're measuring a real phenomenon, not just adding overhead that happens to correlate with something.

## Threats to Validity

Acknowledge up front:

- **Task selection bias** — We chose the questions. Mitigate by having both authors independently propose questions and by including questions we think HCE won't help with.
- **Repo selection bias** — All repos are well-structured open source projects. Results may not generalize to messy internal codebases.
- **Model-specific** — Results are for one model. Other models may benefit differently.
- **Index quality** — HCE's tree-sitter extraction may miss relationships (dynamic dispatch, metaprogramming). The hypergraph is an approximation.
- **Language coverage** — We evaluate 6 of 10 supported languages. TypeScript, Ruby, PHP, and C are not tested.

## Deliverables

1. The experimental harness (open-source script)
2. The full question bank with ground truth answers
3. Raw results (all 1,040 runs with full token/accuracy logs)
4. Analysis notebook
5. The paper

## Next Steps

- [ ] Manuel reviews this protocol
- [ ] Select specific bugs for each repo (requires digging through git history and issues)
- [ ] Write the full question bank (8–12 comprehension questions per repo)
- [ ] Build the harness
- [ ] Run pilot on requests + Django
- [ ] Adjust protocol based on pilot
- [ ] Full evaluation
- [ ] Write the paper
