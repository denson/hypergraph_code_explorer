# Known Bugs

## BUG-001: Tier 1 lookup fails to match short method names to fully-qualified symbols

**Severity:** High — causes HCE to return wrong results for a common query pattern

**Discovered:** Pilot experiment, March 2026

### Symptom

`hce lookup rebuild_auth` fails to find `sessions.SessionRedirectMixin.rebuild_auth` and instead matches a shorter, unrelated symbol (`auth`) via partial token matching.

`hce search rebuild` correctly finds it via Tier 3 text search, confirming the symbol IS in the index.

`hce lookup SessionRedirectMixin` correctly shows `rebuild_auth` as a DEFINES child of the class.

### Root Cause

`retrieval/lookup.py` tokenizes the query string by splitting on common delimiters including underscores. So `rebuild_auth` is tokenized as `["rebuild", "auth"]`. Tier 1 then tries to match each token against node names and finds `auth` as a standalone symbol before it finds the full `sessions.SessionRedirectMixin.rebuild_auth` match. The fully-qualified node exists in the index but the short-name → qualified-name resolution doesn't work for underscore-separated method names.

### Affected Patterns

Any method or function whose name contains an underscore and whose name suffix (after the last `_`) matches a shorter, more prominent symbol in the codebase. Examples:
- `rebuild_auth` → matches `auth` instead
- `rebuild_proxies` → likely matches `proxies`
- `get_redirect_target` → likely matches `target` or `redirect`
- `prepare_cookies` → likely matches `cookies`

### Expected Behavior

`hce lookup rebuild_auth` should match `sessions.SessionRedirectMixin.rebuild_auth` (and any other nodes whose name ends in `rebuild_auth`), not a shorter partial match.

### Fix Direction

In `retrieval/lookup.py`, before tokenizing the query, attempt a direct suffix match against all node names. If any node name ends with `.{query}` or equals `{query}` exactly, prefer those matches over tokenized partial matches. Only fall back to token-split matching if no suffix match is found.

Alternatively, add the short name (final dotted segment, e.g. `rebuild_auth`) as an alias or secondary index key when building the inverted index in `graph/builder.py`, so `rebuild_auth` resolves directly without needing to go through the tokenizer.

### Impact on Pilot Experiment

Task R2 (requests) HCE condition is contaminated by this bug. The agent called `hce_lookup rebuild_auth --callers`, got a near-empty result (82 chars), and fell back to grep + file reading. As a result the R2 HCE runs used **more** tokens than the control condition (141K–151K vs ~111K), inverting the expected result. R2 HCE runs should be repeated after the fix.

### Workaround (for now)

Use `hce search rebuild_auth` (Tier 3 text search) to find the symbol first, then use the fully-qualified name with `hce lookup sessions.SessionRedirectMixin.rebuild_auth`.
