"""Test script: create parallel-view tours on the FastAPI graph."""
from __future__ import annotations

import json
from hypergraph_code_explorer.api import HypergraphSession

CACHE = "target_repos/fastapi/fastapi/.hce_cache"

def main():
    s = HypergraphSession.load(CACHE)

    # --- Tour 1: Security view ---
    plan1 = s.query("how does authentication and authorization work", depth=2)
    tour1 = s.memory_tour_create(
        plan1,
        name="Security: Auth scheme dispatch",
        tags=["security", "auth"],
    )
    print("=== Tour 1: Security ===")
    print(f"  ID:       {tour1.id}")
    print(f"  Name:     {tour1.name}")
    print(f"  Steps:    {len(tour1.steps)}")
    print(f"  Tags:     {tour1.tags}")
    print(f"  Keywords: {tour1.keywords[:10]}")
    print()

    # --- Tour 2: Error handling view ---
    plan2 = s.query("how are exceptions raised and handled", depth=2)
    tour2 = s.memory_tour_create(
        plan2,
        name="Error handling: Exception propagation paths",
        tags=["error-handling", "exceptions"],
    )
    print("=== Tour 2: Error Handling ===")
    print(f"  ID:       {tour2.id}")
    print(f"  Name:     {tour2.name}")
    print(f"  Steps:    {len(tour2.steps)}")
    print(f"  Tags:     {tour2.tags}")
    print(f"  Keywords: {tour2.keywords[:10]}")
    print()

    # --- Tour 3: Hand-crafted (LLM-authored) security tour ---
    tour3_data = {
        "name": "Security: Token validation chain",
        "summary": (
            "Bearer token authentication flows from OAuth2.__call__ through "
            "SecurityBase to individual scheme implementations. Each scheme "
            "calls make_not_authenticated_error on failure."
        ),
        "keywords": [
            "OAuth2", "OAuth2PasswordBearer", "SecurityBase",
            "HTTPBearer", "make_not_authenticated_error",
        ],
        "tags": ["security", "auth", "llm-authored"],
        "steps": [
            {
                "node": "oauth2.OAuth2.__call__",
                "text": (
                    "Entry point for OAuth2 auth. Extracts the Authorization "
                    "header and returns the token string. Raises 401 if "
                    "auto_error=True and no token is present."
                ),
                "file": "security/oauth2.py",
            },
            {
                "node": "SecurityBase",
                "text": (
                    "Abstract base for all security schemes. Subclasses "
                    "implement __call__(request) -> credentials | None. "
                    "Defines make_not_authenticated_error for uniform 401 "
                    "responses."
                ),
                "file": "security/base.py",
            },
            {
                "node": "oauth2.OAuth2PasswordBearer.__call__",
                "text": (
                    "Password bearer flow. Reads Authorization header, "
                    "expects 'Bearer <token>'. Delegates error creation "
                    "to make_not_authenticated_error."
                ),
                "file": "security/oauth2.py",
            },
            {
                "node": "http.HTTPBearer.__call__",
                "text": (
                    "HTTP Bearer scheme. Parses Authorization header into "
                    "scheme + credentials. Returns HTTPAuthorizationCredentials "
                    "model. Same error path via make_not_authenticated_error."
                ),
                "file": "security/http.py",
            },
            {
                "node": "api_key.APIKeyHeader.__call__",
                "text": (
                    "API key via header. Reads a named header (default "
                    "X-API-Key). Different auth mechanism but same error "
                    "contract via SecurityBase."
                ),
                "file": "security/api_key.py",
            },
        ],
    }
    tour3 = s.memory_tour_create_from_dict(tour3_data)
    print("=== Tour 3: Hand-crafted Security Tour ===")
    print(f"  ID:       {tour3.id}")
    print(f"  Name:     {tour3.name}")
    print(f"  Summary:  {tour3.summary}")
    print(f"  Steps:    {len(tour3.steps)}")
    print(f"  Tags:     {tour3.tags}")
    print()

    # --- List all tours, grouped by tag ---
    all_tours = s.memory_tour_list()
    print(f"=== All tours ({len(all_tours)}) ===")
    for t in all_tours:
        status = "PROMOTED" if t["promoted"] else "ephemeral"
        step_count = len(t["steps"])
        tags = ", ".join(t["tags"])
        print(f"  [{status}] {t['name']} ({step_count} steps, tags=[{tags}])")
    print()

    # --- Filter by tag to show the "security" view ---
    security_tours = s.memory_tour_list(tag="security")
    print(f"=== Security view ({len(security_tours)} tours) ===")
    for t in security_tours:
        print(f"  {t['name']}: {t['summary'][:100]}...")
    print()

    # --- Show a few steps from the hand-crafted tour ---
    recalled = s.memory_tour_get(tour3.id)
    print(f"=== Recalled tour: {recalled['name']} (use_count now: {recalled['use_count']}) ===")
    for i, step in enumerate(recalled["steps"]):
        print(f"  Step {i+1}: [{step['node']}]")
        print(f"          {step['text']}")
    print()

    # --- Promote the hand-crafted tour ---
    s.memory_tour_promote(tour3.id)
    print(f"Promoted '{tour3.name}' to durable memory.")
    print()

    # --- Final state ---
    promoted = s.memory_tour_list(promoted_only=True)
    print(f"=== Promoted tours ({len(promoted)}) ===")
    for t in promoted:
        print(f"  {t['name']} (tags=[{', '.join(t['tags'])}])")


if __name__ == "__main__":
    main()
