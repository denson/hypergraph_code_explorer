# Memory Tour Report: FastAPI

**Target codebase**: `fastapi/fastapi/`
**Generated from**: `.hce_cache/memory_tours.json`
**Tour count**: 8
**Promoted tours**: 3
**Total steps**: 230
**Unique files touched**: ~20

---

## Tour Index

| # | Name | Steps | Tags | Promoted | Uses | Origin |
|---|------|------:|------|:--------:|-----:|--------|
| 1 | [Routing System](#1-routing-system) | 118 | `routing` | | 1 | auto-scaffolded |
| 2 | [DI System](#2-di-system) | 9 | `di` | | 1 | text-search |
| 3 | [Validation](#3-validation) | 22 | `validation` | Yes | 1 | auto-scaffolded |
| 4 | [Middleware Pipeline](#4-middleware-pipeline) | 20 | `middleware` | | 0 | auto-scaffolded |
| 5 | [Middleware Stack](#5-middleware-stack-llm-authored) | 4 | `middleware`, `llm-authored` | Yes | 0 | LLM-authored |
| 6 | [Security: Auth scheme dispatch](#6-security-auth-scheme-dispatch) | 11 | `security`, `auth` | | 0 | text-search |
| 7 | [Error handling: Exception propagation paths](#7-error-handling-exception-propagation-paths) | 41 | `error-handling`, `exceptions` | | 0 | auto-scaffolded |
| 8 | [Security: Token validation chain](#8-security-token-validation-chain-llm-authored) | 5 | `security`, `auth`, `llm-authored` | Yes | 1 | LLM-authored |

### Edge type legend

| Edge Type | Notation | Meaning |
|-----------|----------|---------|
| IMPORTS | `[imports]` / `[imported by]` | Module/symbol import relationship |
| CALLS | `[calls]` / `[called by]` | Function/method call site |
| DEFINES | `[defines]` / `[defined in]` | Class/module defines members |
| INHERITS | `[inherits from]` / `[inherited by]` | Class inheritance |
| SIGNATURE | `[has signature]` / `[parameter of]` | Function parameter types |
| RAISES | `[raises]` / `[raised by]` | Exception raise/except sites |
| DECORATES | `[decorates]` / `[decorated by]` | Decorator application |

### Step types

- **Structural steps** have an `edge_type` and show a machine-generated relationship in bracket notation (e.g. `routing [imports] -> starlette.websockets`).
- **Text-match steps** were found by keyword search against the graph, marked with `[text match]`.
- **Frontier steps** are depth-2 expansions that indicate reachable nodes without full detail (e.g. `utils.solve_dependencies — EdgeType.IMPORTS edge at depth 2`).
- **LLM-authored steps** contain narrative text written by an LLM summarizing what a node does in context.

---

## 1. Routing System

| Field | Value |
|-------|-------|
| **ID** | `36d5d25a9199` |
| **Query** | "how does routing work" |
| **Classification** | identifier, broad, structural |
| **Tags** | `routing` |
| **Promoted** | No |
| **Use count** | 1 |
| **Created** | 2026-03-25T08:08:06Z |

**Summary**: 118 steps across 7 files tracing FastAPI's routing system from `routing.py` through dependency injection, exception handling, middleware, security, and ASGI type plumbing.

### Steps (first 15 of 118)

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 1 | `routing` | routing [imports] -> starlette.websockets, WebSocket | `fastapi/routing.py` | IMPORTS |
| 2 | `.routing` | .routing [imported by] -> utils | `fastapi/utils.py` | IMPORTS |
| 3 | `starlette.routing` | starlette.routing [imported by] -> utils | `fastapi/openapi/utils.py` | IMPORTS |
| 4 | `applications` | applications [imports] -> starlette.datastructures, State | `fastapi/applications.py` | IMPORTS |
| 5 | `starlette.websockets` | starlette.websockets [imported by] -> websockets | `fastapi/websockets.py` | IMPORTS |
| 6 | `WebSocket` | WebSocket [imported by] -> \_\_init\_\_ | `fastapi/__init__.py` | IMPORTS |
| 7 | `fastapi.dependencies.models` | fastapi.dependencies.models [imported by] -> utils | `fastapi/openapi/utils.py` | IMPORTS |
| 8 | `Dependant` | Dependant [parameter of] -> routing.get_websocket_app | `fastapi/routing.py` | SIGNATURE |
| 9 | `fastapi.exceptions` | fastapi.exceptions [imported by] -> oauth2 | `fastapi/security/oauth2.py` | IMPORTS |
| 10 | `EndpointContext` | EndpointContext [called by] -> routing.serialize_response | `fastapi/routing.py` | CALLS |
| 11 | `FastAPIError` | FastAPIError [inherited by] -> exceptions.PydanticV1NotSupportedError | `fastapi/exceptions.py` | INHERITS |
| 12 | `RequestValidationError` | RequestValidationError [parameter of] -> exception_handlers.request_validation_exception_handler | `fastapi/exception_handlers.py` | SIGNATURE |
| 13 | `ResponseValidationError` | ResponseValidationError [raised by] -> routing.serialize_response | `fastapi/routing.py` | RAISES |
| 14 | `WebSocketRequestValidationError` | WebSocketRequestValidationError [raised by] -> routing.get_websocket_app | `fastapi/routing.py` | RAISES |
| 15 | `starlette.concurrency` | starlette.concurrency [imported by] -> concurrency | `fastapi/concurrency.py` | IMPORTS |

### Remaining steps breakdown

**By edge type** (steps 16-118):

| Edge Type | Count |
|-----------|------:|
| IMPORTS | 36 |
| CALLS | 14 |
| SIGNATURE | 12 |
| DEFINES | 4 |
| INHERITS | 4 |
| RAISES | 0 |
| *(frontier/depth-2)* | 33 |

**Key structural steps** (selected from steps 16-67):

- `routing._DefaultLifespan` DEFINES `__init__`, `__aenter__`, `__aexit__`, `__call__`
- `routing.APIWebSocketRoute` INHERITS from `routing.WebSocketRoute`
- `routing.APIRouter` DEFINES 19 methods including `add_api_route`, `include_router`, `get`, `post`, `put`, `delete`, `patch`, `options`, `head`, `trace`, `websocket`
- `routing.serialize_response` has a complex SIGNATURE with `ModelField`, `IncEx`, `EndpointContext`, etc.
- `_should_embed_body_fields` [called by] `routing.APIWebSocketRoute.__init__`
- `get_value_or_default` [called by] `routing.APIRouter.add_api_route`
- `DefaultPlaceholder` [called by] `datastructures.Default`

**Frontier nodes** (steps 68-118): 51 nodes reachable at depth 2, spanning `fastapi/dependencies/utils.py` (20 functions), `fastapi/middleware/asyncexitstack.py`, `fastapi/openapi/docs.py`, `fastapi/security/oauth2.py`, and `fastapi/_compat/shared.py`.

---

## 2. DI System

| Field | Value |
|-------|-------|
| **ID** | `65d316379306` |
| **Query** | "how does dependency injection work" |
| **Classification** | text_search, broad, identifier, structural |
| **Tags** | `di` |
| **Promoted** | No |
| **Use count** | 1 |
| **Created** | 2026-03-25T08:08:06Z |

**Summary**: 9 steps across 3 files found by text-search for dependency injection concepts. Focuses on `DependencyCacheKey`, `SolvedDependency`, and the dependency resolution machinery in `dependencies/utils.py`.

### Steps

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 1 | `DependencyCacheKey` | DependencyCacheKey [text match] | `fastapi/dependencies/models.py` | SIGNATURE |
| 2 | `DependencyScopeError` | DependencyScopeError [text match] | `fastapi/dependencies/utils.py` | RAISES |
| 3 | `utils.add_non_field_param_to_dependency` | utils.add_non_field_param_to_dependency [text match] | `fastapi/dependencies/utils.py` | SIGNATURE |
| 4 | `list[DependencyCacheKey] \| None` | list[DependencyCacheKey] \| None [text match] | `fastapi/dependencies/utils.py` | SIGNATURE |
| 5 | `add_non_field_param_to_dependency` | add_non_field_param_to_dependency [text match] | `fastapi/dependencies/utils.py` | CALLS |
| 6 | `SolvedDependency` | SolvedDependency [text match] | `fastapi/dependencies/utils.py` | SIGNATURE |
| 7 | `getattr(...).get` | getattr( dependency_overrides_provider, "dependency_overrides", {} ).get [text match] | `fastapi/dependencies/utils.py` | CALLS |
| 8 | `dict[DependencyCacheKey, Any] \| None` | dict[DependencyCacheKey, Any] \| None [text match] | `fastapi/dependencies/utils.py` | SIGNATURE |
| 9 | `exceptions.DependencyScopeError` | exceptions.DependencyScopeError [text match] | `fastapi/exceptions.py` | DEFINES |

---

## 3. Validation

| Field | Value |
|-------|-------|
| **ID** | `99e511882e60` |
| **Query** | "how does request validation work" |
| **Classification** | identifier, broad, structural |
| **Tags** | `validation` |
| **Promoted** | **Yes** |
| **Use count** | 1 |
| **Created** | 2026-03-25T08:08:06Z |

**Summary**: 22 steps across 3 files tracing request validation from the `Request` object through security schemes (`OAuth2`, `HTTPBearer`, `APIKey`, `OpenIdConnect`) to exception handlers. Covers how each security scheme's `__call__` method validates incoming requests and how validation errors propagate.

### Steps

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 1 | `Request` | Request [imported by] -> oauth2 | `fastapi/security/oauth2.py` | IMPORTS |
| 2 | `oauth2` | oauth2 [imports] -> starlette.status, HTTP_401_UNAUTHORIZED | `fastapi/security/oauth2.py` | IMPORTS |
| 3 | `requests` | requests [imports] -> starlette.requests, HTTPConnection | `fastapi/requests.py` | IMPORTS |
| 4 | `oauth2.OAuth2.__call__` | oauth2.OAuth2.__call__ [defined in] -> oauth2.OAuth2 | `fastapi/security/oauth2.py` | DEFINES |
| 5 | `exception_handlers.http_exception_handler` | exception_handlers.http_exception_handler [calls] -> JSONResponse, Response, is_body_allowed_for_status_code, getattr | `fastapi/exception_handlers.py` | CALLS |
| 6 | `applications` | applications [imports] -> starlette.datastructures, State | `fastapi/applications.py` | IMPORTS |
| 7 | `open_id_connect_url` | open_id_connect_url [imports] -> starlette.status, HTTP_401_UNAUTHORIZED | `fastapi/security/open_id_connect_url.py` | IMPORTS |
| 8 | `routing.request_response` | routing.request_response [raises] -> FastAPIError | `fastapi/routing.py` | RAISES |
| 9 | `http.HTTPBearer.__call__` | http.HTTPBearer.__call__ [defined in] -> http.HTTPBearer | `fastapi/security/http.py` | DEFINES |
| 10 | `api_key` | api_key [imports] -> typing, Annotated | `fastapi/security/api_key.py` | IMPORTS |
| 11 | `exception_handlers.request_validation_exception_handler` | exception_handlers.request_validation_exception_handler [calls] -> JSONResponse, jsonable_encoder, exc.errors | `fastapi/exception_handlers.py` | CALLS |
| 12 | `http` | http [imports] -> fastapi.openapi.models, HTTPBaseModel | `fastapi/security/http.py` | IMPORTS |
| 13 | `exception_handlers` | exception_handlers [imports] -> starlette.exceptions, HTTPException | `fastapi/exception_handlers.py` | IMPORTS |
| 14 | `api_key.APIKeyCookie.__call__` | api_key.APIKeyCookie.__call__ [defined in] -> api_key.APIKeyCookie | `fastapi/security/api_key.py` | DEFINES |
| 15 | `api_key.APIKeyHeader.__call__` | api_key.APIKeyHeader.__call__ [calls] -> self.check_api_key, request.headers.get | `fastapi/security/api_key.py` | CALLS |
| 16 | `open_id_connect_url.OpenIdConnect.__call__` | open_id_connect_url.OpenIdConnect.__call__ [defined in] -> open_id_connect_url.OpenIdConnect | `fastapi/security/open_id_connect_url.py` | DEFINES |
| 17 | `api_key.APIKeyQuery.__call__` | api_key.APIKeyQuery.__call__ [defined in] -> api_key.APIKeyQuery | `fastapi/security/api_key.py` | DEFINES |
| 18 | `http.HTTPDigest.__call__` | http.HTTPDigest.__call__ [raises] -> self.make_not_authenticated_error | `fastapi/security/http.py` | RAISES |
| 19 | `http.HTTPBase.__call__` | http.HTTPBase.__call__ [raises] -> self.make_not_authenticated_error | `fastapi/security/http.py` | RAISES |
| 20 | `oauth2.OAuth2PasswordBearer.__call__` | oauth2.OAuth2PasswordBearer.__call__ [raises] -> self.make_not_authenticated_error | `fastapi/security/oauth2.py` | RAISES |
| 21 | `oauth2.OAuth2AuthorizationCodeBearer.__call__` | oauth2.OAuth2AuthorizationCodeBearer.__call__ [raises] -> self.make_not_authenticated_error | `fastapi/security/oauth2.py` | RAISES |
| 22 | `http.HTTPBasic.__call__` | http.HTTPBasic.__call__ [raises] -> self.make_not_authenticated_error | `fastapi/security/http.py` | RAISES |

---

## 4. Middleware Pipeline

| Field | Value |
|-------|-------|
| **ID** | `0640b09539f3` |
| **Query** | "how does middleware work" |
| **Classification** | identifier, broad, structural |
| **Tags** | `middleware` |
| **Promoted** | No |
| **Use count** | 0 |
| **Created** | 2026-03-25T08:10:08Z |

**Summary**: 20 steps across 3 files. Traces middleware from `Middleware` import through `FastAPI.middleware` decorator, `FastAPI.build_middleware_stack`, and into the `APIRouter` method signatures that share the `Callable[[DecoratedCallable], DecoratedCallable]` return type.

### Steps

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 1 | `Middleware` | Middleware [imported by] -> \_\_init\_\_ | `fastapi/middleware/__init__.py` | IMPORTS |
| 2 | `starlette.middleware` | starlette.middleware [imported by] -> \_\_init\_\_ | `fastapi/middleware/__init__.py` | IMPORTS |
| 3 | `applications.FastAPI.middleware` | applications.FastAPI.middleware [has signature] -> Callable[[DecoratedCallable], DecoratedCallable] | `fastapi/applications.py` | SIGNATURE |
| 4 | `Callable[[DecoratedCallable], DecoratedCallable]` | Callable[[DecoratedCallable], DecoratedCallable] [parameter of] -> applications.FastAPI.patch | `fastapi/applications.py` | SIGNATURE |
| 5 | `applications.FastAPI` | applications.FastAPI [defined in] -> applications | `fastapi/applications.py` | DEFINES |
| 6 | `applications` | applications [imports] -> starlette.responses, HTMLResponse, JSONResponse, Response | `fastapi/applications.py` | IMPORTS |
| 7 | `applications.FastAPI.build_middleware_stack` | applications.FastAPI.build_middleware_stack [has signature] -> ASGIApp | `fastapi/applications.py` | SIGNATURE |

### Frontier steps (depth 2)

Steps 8-20 are frontier nodes from `fastapi/routing.py`, all reachable via the shared `Callable[[DecoratedCallable], DecoratedCallable]` signature type:

`routing.APIRouter.put`, `routing.APIRouter.get`, `routing.APIRouter.api_route`, `routing.APIRouter.trace`, `routing.APIRouter.head`, `routing.APIRouter.websocket`, `routing.APIRouter.on_event`, `routing.APIRouter.options`, `routing.APIRouter.post`, `routing.APIRouter.websocket_route`, `routing.APIRouter.patch`, `routing.APIRouter.delete`, `routing.APIRouter.route`

---

## 5. Middleware Stack (LLM-authored)

| Field | Value |
|-------|-------|
| **ID** | `9679ae03be49` |
| **Query** | *(none -- hand-crafted)* |
| **Tags** | `middleware`, `llm-authored` |
| **Promoted** | **Yes** |
| **Use count** | 0 |
| **Created** | 2026-03-25T08:10:08Z |

**Summary**: FastAPI middleware is a chain of ASGI wrappers. Each middleware wraps the next, forming a pipeline that processes requests inward and responses outward.

### Steps

| # | Node | Narrative | File |
|---|------|-----------|------|
| 1 | `cors` | CORSMiddleware wraps the ASGI app to handle cross-origin requests | `middleware/cors.py` |
| 2 | `asyncexitstack.AsyncExitStackMiddleware` | AsyncExitStackMiddleware manages async context managers across the request lifecycle | `middleware/asyncexitstack.py` |
| 3 | `gzip` | GZipMiddleware compresses responses above a minimum size threshold | `middleware/gzip.py` |
| 4 | `trustedhost` | TrustedHostMiddleware validates the Host header against an allowlist | `middleware/trustedhost.py` |

This tour demonstrates the **LLM-authored** format: each step has free-text narrative explaining what the node does in context, rather than machine-generated relationship notation.

---

## 6. Security: Auth scheme dispatch

| Field | Value |
|-------|-------|
| **ID** | `9858f1e4b2a2` |
| **Query** | "how does authentication and authorization work" |
| **Classification** | text_search, broad, identifier, structural |
| **Tags** | `security`, `auth` |
| **Promoted** | No |
| **Use count** | 0 |
| **Created** | 2026-03-25T08:43:48Z |

**Summary**: 11 steps across 2 files found by text-search. Maps the authentication dispatch from `get_authorization_scheme_param` through `HTTPAuthorizationCredentials` to `OAuth2AuthorizationCodeBearer` and its `__call__`/`__init__` methods.

### Steps

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 1 | `authorization_header_value.partition` | authorization_header_value.partition [text match] | `fastapi/security/utils.py` | CALLS |
| 2 | `models.OAuthFlowAuthorizationCode` | models.OAuthFlowAuthorizationCode [text match] | `fastapi/openapi/models.py` | INHERITS |
| 3 | `HTTPAuthorizationCredentials` | HTTPAuthorizationCredentials [text match] | `fastapi/security/__init__.py` | IMPORTS |
| 4 | `OAuth2AuthorizationCodeBearer` | OAuth2AuthorizationCodeBearer [text match] | `fastapi/security/__init__.py` | IMPORTS |
| 5 | `http.HTTPAuthorizationCredentials` | http.HTTPAuthorizationCredentials [text match] | `fastapi/security/http.py` | INHERITS |
| 6 | `get_authorization_scheme_param` | get_authorization_scheme_param [text match] | `fastapi/security/http.py` | IMPORTS |
| 7 | `HTTPAuthorizationCredentials \| None` | HTTPAuthorizationCredentials \| None [text match] | `fastapi/security/http.py` | SIGNATURE |
| 8 | `oauth2.OAuth2AuthorizationCodeBearer` | oauth2.OAuth2AuthorizationCodeBearer [text match] | `fastapi/security/oauth2.py` | INHERITS |
| 9 | `oauth2.OAuth2AuthorizationCodeBearer.__call__` | oauth2.OAuth2AuthorizationCodeBearer.__call__ [text match] | `fastapi/security/oauth2.py` | RAISES |
| 10 | `oauth2.OAuth2AuthorizationCodeBearer.__init__` | oauth2.OAuth2AuthorizationCodeBearer.__init__ [text match] | `fastapi/security/oauth2.py` | DEFINES |
| 11 | `utils.get_authorization_scheme_param` | utils.get_authorization_scheme_param [text match] | `fastapi/security/utils.py` | DEFINES |

---

## 7. Error handling: Exception propagation paths

| Field | Value |
|-------|-------|
| **ID** | `8087f3be5d25` |
| **Query** | "how are exceptions raised and handled" |
| **Classification** | identifier, structural |
| **Tags** | `error-handling`, `exceptions` |
| **Promoted** | No |
| **Use count** | 0 |
| **Created** | 2026-03-25T08:43:48Z |

**Summary**: 41 steps across 7 files. Comprehensive map of FastAPI's exception hierarchy and propagation paths, from the `exceptions` module definitions through every importer (`applications`, `exception_handlers`, `security/*`, `encoders`) to the class inheritance tree and downstream consumers.

### Steps: Exception definitions (steps 1-5)

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 1 | `exceptions` | exceptions [defines] -> EndpointContext, HTTPException, WebSocketException, FastAPIError, DependencyScopeError, ValidationException, RequestValidationError, WebSocketRequestValidationError, ResponseValidationError, PydanticV1NotSupportedError, FastAPIDeprecationWarning | `fastapi/exceptions.py` | DEFINES |
| 2 | `.exceptions` | .exceptions [imported by] -> \_\_init\_\_ | `fastapi/__init__.py` | IMPORTS |
| 3 | `fastapi.exceptions` | fastapi.exceptions [imported by] -> applications | `fastapi/applications.py` | IMPORTS |
| 4 | `starlette.exceptions` | starlette.exceptions [imported by] -> applications | `fastapi/applications.py` | IMPORTS |
| 5 | `starlette.middleware.exceptions` | starlette.middleware.exceptions [imported by] -> applications | `fastapi/applications.py` | IMPORTS |

### Steps: Importers (steps 6-14)

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 6 | `applications` | applications [imports] -> typing_extensions, deprecated | `fastapi/applications.py` | IMPORTS |
| 7 | `params` | params [imported by] -> utils | `fastapi/dependencies/utils.py` | IMPORTS |
| 8 | `exception_handlers` | exception_handlers [imports] -> starlette.requests, Request | `fastapi/exception_handlers.py` | IMPORTS |
| 9 | `responses` | responses [imports] -> starlette.responses, StreamingResponse | `fastapi/responses.py` | IMPORTS |
| 10 | `http` | http [imports] -> fastapi.openapi.models, HTTPBaseModel | `fastapi/security/http.py` | IMPORTS |
| 11 | `oauth2` | oauth2 [defines] -> OAuth2PasswordRequestForm, OAuth2PasswordRequestFormStrict, OAuth2, OAuth2PasswordBearer, OAuth2AuthorizationCodeBearer, SecurityScopes | `fastapi/security/oauth2.py` | DEFINES |
| 12 | `encoders` | encoders [imports] -> pydantic_core, PydanticUndefinedType | `fastapi/encoders.py` | IMPORTS |
| 13 | `api_key` | api_key [imports] -> annotated_doc, Doc | `fastapi/security/api_key.py` | IMPORTS |
| 14 | `open_id_connect_url` | open_id_connect_url [defines] -> OpenIdConnect | `fastapi/security/open_id_connect_url.py` | DEFINES |

### Steps: Exception class hierarchy (steps 15-25)

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 15 | `exceptions.EndpointContext` | exceptions.EndpointContext [inherits from] -> TypedDict | `fastapi/exceptions.py` | INHERITS |
| 16 | `exceptions.HTTPException` | exceptions.HTTPException [inherits from] -> StarletteHTTPException | `fastapi/exceptions.py` | INHERITS |
| 17 | `exceptions.WebSocketException` | exceptions.WebSocketException [defines] -> \_\_init\_\_ | `fastapi/exceptions.py` | DEFINES |
| 18 | `exceptions.FastAPIError` | exceptions.FastAPIError [inherits from] -> RuntimeError | `fastapi/exceptions.py` | INHERITS |
| 19 | `exceptions.DependencyScopeError` | exceptions.DependencyScopeError [inherits from] -> FastAPIError | `fastapi/exceptions.py` | INHERITS |
| 20 | `exceptions.ValidationException` | exceptions.ValidationException [defines] -> \_\_init\_\_, errors, \_format\_endpoint\_context, \_\_str\_\_ | `fastapi/exceptions.py` | DEFINES |
| 21 | `exceptions.RequestValidationError` | exceptions.RequestValidationError [inherits from] -> ValidationException | `fastapi/exceptions.py` | INHERITS |
| 22 | `exceptions.WebSocketRequestValidationError` | exceptions.WebSocketRequestValidationError [defines] -> \_\_init\_\_ | `fastapi/exceptions.py` | DEFINES |
| 23 | `exceptions.ResponseValidationError` | exceptions.ResponseValidationError [defines] -> \_\_init\_\_ | `fastapi/exceptions.py` | DEFINES |
| 24 | `exceptions.PydanticV1NotSupportedError` | exceptions.PydanticV1NotSupportedError [inherits from] -> FastAPIError | `fastapi/exceptions.py` | INHERITS |
| 25 | `exceptions.FastAPIDeprecationWarning` | exceptions.FastAPIDeprecationWarning [inherits from] -> UserWarning | `fastapi/exceptions.py` | INHERITS |

### Steps: Type system and models (steps 26-31)

| # | Node | Relationship | File | Type |
|---|------|-------------|------|------|
| 26 | `typing` | typing [imported by] -> models | `fastapi/dependencies/models.py` | IMPORTS |
| 27 | `TypedDict` | TypedDict [imported by] -> models | `fastapi/openapi/models.py` | IMPORTS |
| 28 | `pydantic` | pydantic [imported by] -> v2 | `fastapi/_compat/v2.py` | IMPORTS |
| 29 | `BaseModel` | BaseModel [inherited by] -> models.BaseModelWithConfig | `fastapi/openapi/models.py` | INHERITS |
| 30 | `create_model` | create_model [called by] -> v2.create_body_model | `fastapi/_compat/v2.py` | CALLS |
| 31 | `annotated_doc` | annotated_doc [imported by] -> background | `fastapi/background.py` | IMPORTS |

### Frontier steps (steps 32-41)

10 nodes reachable at depth 2:

- `collections.abc`, `param_functions` (15 files match), `models`, `shared`, `v2`, `v2.create_body_model` from `fastapi/_compat/`
- `models.Example`, `models.BaseModelWithConfig`, `models.Reference`, `models.Discriminator` from `fastapi/openapi/`

---

## 8. Security: Token validation chain (LLM-authored)

| Field | Value |
|-------|-------|
| **ID** | `1e9746af6287` |
| **Query** | *(none -- hand-crafted)* |
| **Tags** | `security`, `auth`, `llm-authored` |
| **Promoted** | **Yes** |
| **Use count** | 1 |
| **Created** | 2026-03-25T08:43:48Z |

**Summary**: Bearer token authentication flows from `OAuth2.__call__` through `SecurityBase` to individual scheme implementations. Each scheme calls `make_not_authenticated_error` on failure.

### Steps

| # | Node | Narrative | File |
|---|------|-----------|------|
| 1 | `oauth2.OAuth2.__call__` | Entry point for OAuth2 auth. Extracts the Authorization header and returns the token string. Raises 401 if auto_error=True and no token is present. | `security/oauth2.py` |
| 2 | `SecurityBase` | Abstract base for all security schemes. Subclasses implement \_\_call\_\_(request) -> credentials \| None. Defines make_not_authenticated_error for uniform 401 responses. | `security/base.py` |
| 3 | `oauth2.OAuth2PasswordBearer.__call__` | Password bearer flow. Reads Authorization header, expects 'Bearer \<token\>'. Delegates error creation to make_not_authenticated_error. | `security/oauth2.py` |
| 4 | `http.HTTPBearer.__call__` | HTTP Bearer scheme. Parses Authorization header into scheme + credentials. Returns HTTPAuthorizationCredentials model. Same error path via make_not_authenticated_error. | `security/http.py` |
| 5 | `api_key.APIKeyHeader.__call__` | API key via header. Reads a named header (default X-API-Key). Different auth mechanism but same error contract via SecurityBase. | `security/api_key.py` |

This tour demonstrates a **promoted, LLM-authored** tour: a curated narrative walkthrough of the token validation chain, telling a story about how auth schemes share a common error contract through `SecurityBase.make_not_authenticated_error`.

---

## Observations

### Auto-scaffolded vs. LLM-authored

The eight tours split into two distinct formats:

- **Auto-scaffolded** (tours 1-4, 6-7): Generated from graph queries. Steps use bracket notation (`[imports]`, `[calls]`, `[raises]`) to state structural relationships. High step counts (9-118). Good for exhaustive structural coverage but require interpretation by the reader.

- **LLM-authored** (tours 5, 8): Hand-crafted with narrative step text. Low step counts (4-5). Curated to tell a story. Both are promoted, reflecting higher confidence in their accuracy and durability.

### Tag distribution

| Tag | Tours |
|-----|------:|
| `security` / `auth` | 3 |
| `middleware` | 2 |
| `routing` | 1 |
| `validation` | 1 |
| `di` | 1 |
| `error-handling` / `exceptions` | 1 |
| `llm-authored` | 2 |

### Promotion patterns

Three tours are promoted. Two are LLM-authored (curated, narrative), one is auto-scaffolded (Validation). This suggests promotion correlates with either manual curation or verified utility (the Validation tour was used once and then promoted).
