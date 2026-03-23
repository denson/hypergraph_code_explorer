# HCE Query Guide

Detailed patterns for getting the most out of HCE's query tools.

## hce_lookup — Structural Traversal

The most precise tool. Use when you know or can guess the symbol name.

### Parameters

- `symbol` (required): Symbol name, e.g., "FastAPI", "Session.send", "get_request_handler"
- `calls` (bool): Show what this symbol calls
- `callers` (bool): Show what calls this symbol
- `inherits` (bool): Show inheritance relationships
- `imports` (bool): Show import relationships
- `depth` (int): How many levels to traverse (default 1)

### Common Patterns

**"What does this class do?"**
```
hce_lookup(symbol="ClassName", calls=True, depth=1)
```

**"What depends on this function?"**
```
hce_lookup(symbol="function_name", callers=True, depth=1)
```

**"Show the full call chain from entry point"**
```
hce_lookup(symbol="main", calls=True, depth=3)
```

**"What's the class hierarchy?"**
```
hce_lookup(symbol="BaseClass", inherits=True, depth=2)
```

**"What calls what, both directions"**
```
hce_lookup(symbol="middleware", calls=True, callers=True, depth=1)
```

## hce_search — Text Search

Finds symbols by substring match on names, file paths, and relations. Use when you don't know the exact symbol name.

### Parameters

- `term` (required): Search string, e.g., "auth", "middleware", "validate"
- `max_results` (int): Cap on results (default 20)

### Common Patterns

**"Find everything related to authentication"**
```
hce_search(term="auth")
```

**"What modules deal with routing?"**
```
hce_search(term="route")
```

**"Are there any test utilities?"**
```
hce_search(term="test")
```

## hce_query — Natural Language

Routes through multiple retrieval tiers: exact lookup, structural traversal, and text search. Best for open-ended questions.

### Parameters

- `query` (required): Natural language question
- `depth` (int): Traversal depth for structural expansion (default 2)

### Common Patterns

**Architectural questions:**
```
hce_query(query="how does dependency injection work")
hce_query(query="what is the request lifecycle")
hce_query(query="how are errors handled")
```

**Specific behavior:**
```
hce_query(query="what happens when a request comes in")
hce_query(query="how does the ORM build queries")
```

## hce_overview — Big Picture

Returns the top symbols ranked by structural centrality, plus module breakdown.

### Parameters

- `top` (int): Number of top symbols (default 10)

### When to Use

- Starting exploration of an unfamiliar codebase
- Identifying the "load-bearing" symbols
- Understanding the module structure

## hce_stats — Graph Metrics

Returns node count, edge count, type breakdown, and hub nodes.

### When to Use

- Verifying the index completed successfully
- Understanding codebase scale
- Identifying the most connected nodes

## Exploration Strategy

For an unfamiliar codebase, follow this sequence:

1. `hce_stats()` — Verify index, understand scale
2. `hce_overview(top=20)` — Find the important symbols
3. `hce_search(term="<domain term>")` — Find symbols in areas of interest
4. `hce_lookup(symbol="<key symbol>", calls=True, depth=2)` — Trace call chains
5. `hce_query(query="<specific question>")` — Ask targeted questions
6. Read source files only for the specific functions where graph structure isn't enough
